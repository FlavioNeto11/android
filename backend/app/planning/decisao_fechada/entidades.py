"""Remoção de entidades por LISTA DE PERMISSÃO, que falha fechada, para a C3 da intenção (item 31.9, ADR-069 item 4).

A C3 é o comando do dono depois de `sem_destinos` e de `redact`: texto livre, que pode trazer nome de terceiro, `@handle`,
e-mail, número, telefone, endereço e link. A regra do dono é "C3 só depois de remoção de entidades que FALHA FECHADA". Uma
lista de BLOQUEIO (achar o nome e trocá-lo) falha aberta: o nome em minúsculas, o primeiro termo da frase, o handle sem
`@` e o nome fora do Latin-1 passam (revisão independente de 02/10, `.claude/handoffs/revisao-31-9.md`). Por isso aqui é
o contrário (`remover_entidades`):

0. o texto é normalizado (`normalizar`: NFKC, sem marca combinante nem caractere invisível, todo traço como "-"): letra
   circulada, de largura cheia ou matemática vira a letra comum, e "a<ZWSP>na" vira "ana", julgada inteira. Palavra que
   mistura alfabetos (o "а" cirílico em "senhа") RECUSA o texto;
1. o que tem forma conhecida vira marcador fixo: o que está entre aspas (`[texto]`: é o que a pessoa manda escrever),
   link, e-mail, `@handle`, domínio, telefone e número. Aspa que SOBRA (desbalanceada ou de outro sistema) recusa. Símbolo
   (emoji, indicador regional, braille, letra em quadrado negativo) vira `[texto]`; colado entre duas letras, recusa;
2. o que não tem como ser mascarado com segurança RECUSA o texto inteiro (`None`): endereço (rua, avenida, quadra...),
   documento (CPF, RG...), e-mail escrito por extenso ou ofuscado (`arroba`, `(at)`, `{dot}`, `ponto com`), sobra de `@`
   ou `://` e DOIS ou mais numerais por extenso (telefone, documento ou PIN ditado). UM numeral vira `[numero]`;
3. TODA palavra que sobra só fica se estiver no vocabulário PERMITIDO: palavras funcionais e de comando (`_COMUNS`) e os
   nomes de app que quem chama passa (`vocabulario`: o id do app e os rótulos do registro, ADR-052). O resto vira
   `[termo]`: "joana curtiu isso", "Joana: abra o app", "send a message to john" e "siga joana_silva99" saem com
   `[termo]` no lugar do nome. Uma trava a mais, que só mascara: a palavra com maiúscula fora do início da frase vira
   `[termo]` mesmo permitida (o "Uma" de "send a message to Uma", o "Do" de "Do Van Minh"), salvo nome de app ("abra o
   Outlook");
4. se a proporção de palavras trocadas passar de `LIMIAR_DESCONHECIDAS` (ou forem mais de `MAX_DESCONHECIDAS`), o texto
   inteiro é recusado: o que sobra é sobretudo máscara, mede pouco e a forma da frase já diz demais.

O vocabulário NÃO cresce com o catálogo (reverificação de 03/10, `.claude/handoffs/reverificacao-31-9.md`): o nome de
fluxo legado é o resumo que a IA fez de um comando antigo (`flows.name = plan.summary[:120]`), com o destino dentro, e
liberava "flavio", "neto" e handles de terceiros. O catálogo (C2) vai só nas opções da R2, por `mascarar_catalogo`: as
mesmas máscaras de forma e a regra da maiúscula, sem recusa (a opção precisa existir) e sem lista de permissão (C2 é
liberado; o nome em minúsculas dentro do nome de um fluxo é risco residual, decisão do dono).

Nome de persona NOSSA e nome ou pacote de app podem ir sem máscara (decisão do dono de 03/10 00:15Z, emenda ao ADR-069):
não há lista de bloqueio do banco. O nome de persona fora do vocabulário vira `[termo]` como qualquer outro, o que só
custa utilidade.

A caixa do texto não importa para a lista de permissão (o `TargetExtractor` pode devolver o comando todo em minúsculas,
revisão, achado 3): a comparação é por palavra normalizada (casefold, sem acento), e a regra da maiúscula só vale em texto
que tem minúscula. Credencial e 2FA (C7) NÃO são tratados aqui: o consumidor recusa o pedido inteiro antes (`intencao.py`).

Funções puras: sem banco, sem rede, sem configuração.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Final

#: Marcadores fixos, minúsculos e entre colchetes: nenhum é palavra do vocabulário, e a conferência os ignora.
M_URL: Final = "[link]"
M_EMAIL: Final = "[email]"
M_HANDLE: Final = "[usuario]"
M_TELEFONE: Final = "[telefone]"
M_NUMERO: Final = "[numero]"
M_TERMO: Final = "[termo]"
M_TEXTO: Final = "[texto]"

_MARCADORES: Final = (M_URL, M_EMAIL, M_HANDLE, M_TELEFONE, M_NUMERO, M_TERMO, M_TEXTO)
#: O miolo de cada marcador ("link", "termo"...): entre colchetes, não é palavra do texto.
_MIOLOS: Final = frozenset(m[1:-1] for m in _MARCADORES)

#: Acima desta fração de palavras desconhecidas (trocadas por `[termo]`), o texto inteiro é recusado.
LIMIAR_DESCONHECIDAS: Final = 0.5
#: E acima deste número absoluto, também (um comando longo cheio de nomes não sai "com metade mascarada").
MAX_DESCONHECIDAS: Final = 6

# ------------------------------------------------------------------ 1. troca por forma (do mais específico ao mais geral)
#: Aspas simples ASCII e crase só fora de palavra: o apóstrofo de "D'Ávila" não abre trecho. Depois do NFKC o "″" vira
#: "′′" e as aspas de largura cheia viram ASCII. Aspa que SOBRA (desbalanceada, de outro sistema, pontuação de abrir ou
#: fechar citação, colchete que não é ASCII) recusa o texto: `_primeira_aspa_sobrando`.
_ASPAS = re.compile(r'"[^"]*"|“[^”]*”|„[^“”]*[“”]|«[^»]*»|‹[^›]*›|‘[^’]*’|‚[^‘’]*[‘’]|「[^」]*」|『[^』]*』'
                    r"|〝[^〞〟]*[〞〟]|′+[^′]*′+|(?<!\w)'[^'\n]*'(?!\w)|`[^`]*`")
_CARACTERES_DE_ASPA: Final = frozenset("\"'`′″‴‵‶‷„‚“”‘’«»‹›「」『』〝〞〟❛❜❝❞")
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*")
_HANDLE = re.compile(r"@\w[\w.]*")
_DOMINIO = re.compile(r"(?i)\b[\w\-]+(?:\.[\w\-]+)*\.[a-z]{2,}(?:/\S*)?\b")
_TELEFONE = re.compile(r"(?:\+?\d{1,3}[\s.\-])?\(?\d{2}\)?[\s.\-]?\d{4,5}[\s.\-]?\d{4}")
#: Qualquer número (também quebrado por UM separador entre dígitos: "12 34 56", "1.234", "11-98"). Até "pedido 12" é
#: identificador; a contagem ("curta 25 posts") perde pouco como `[numero]`.
_NUMERO = re.compile(r"\d(?:[\s.\-/]?\d)*")
#: Três ou mais algarismos por extenso em sequência, também ligados por "e", "y" ou "and" ("nove nove oito sete", "um e
#: um e um"): é telefone ou documento ditado. Recusa. Pega também "um", "uma", "uno" e "dos", que `_NUMERAIS` não conta.
_ALGARISMO_FALADO: Final = (r"(?:zero|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|meia|sete|oito|nove|one|two|three|four"
                            r"|five|six|seven|eight|nine|cero|uno|una|dos|cuatro|siete|ocho|nueve)")
_DITADO = re.compile(rf"(?i)\b(?:{_ALGARISMO_FALADO}(?:\W+(?:e|y|and)\W+|\W+)){{2,}}{_ALGARISMO_FALADO}\b")

# ------------------------------------------------------------------ 2. recusa (não há máscara segura)
_RECUSA = re.compile(
    r"@|://|(?i:\bwww\b)|(?i:\barroba\b)|(?i:\bponto\s*com\b)|(?i:\bdot\s*com\b)"
    r"|(?i:[(\[{<]\s*(?:at|dot|arroba|ponto)\s*[)\]}>])"
    # endereço: o logradouro seguido de qualquer termo (o nome da rua é dado de lugar de uma pessoa)
    r"|(?i:\b(?:rua|r\.|avenida|av\.?|travessa|tv\.|alameda|al\.|pra[çc]a|rodovia|estrada|largo|viela|cep|bairro"
    r"|apartamento|apto|bloco|condom[ií]nio|quadra|lote|casa|vila|calle|plaza|paseo|carretera|barrio)\b)"
    r"|(?i:\b(?:street|st\.|avenue|ave\.|road|rd\.|lane|blvd|boulevard|zip)\b)"
    # documento: o número que vem junto (em algarismo ou por extenso) identifica a pessoa
    r"|(?i:\b(?:cpf|cnpj|rg|cnh|passaporte|passport|ssn|dni|nif)\b)")
_SOBRA_DIGITOS = re.compile(r"\d\D{0,2}\d\D{0,2}\d")
_PARENTESES_COM_DIGITO = re.compile(r"\(\s*\d")

#: Numerais por extenso (normalizados, sem acento). UM só vira `[numero]`; DOIS ou mais no texto, ligados por qualquer coisa
#: ("nove e oito", "nove oito, depois sete seis", "dez dez"), recusam: é telefone, documento ou PIN ditado
#: (reverificação do 31.9, 03/10). "um", "uma", "one", "uno" e "una" ficam de fora porque são artigo, e "dos" (espanhol)
#: porque em português é "de + os"; o `_DITADO` ainda pega "um um um" e "dos dos dos". O numeral não conta no limiar.
_NUMERAIS: Final[frozenset[str]] = frozenset("""
zero dois duas tres quatro cinco seis meia sete oito nove dez onze doze treze catorze quatorze quinze dezesseis dezasseis
dezessete dezassete dezoito dezenove dezanove vinte trinta quarenta cinquenta sessenta setenta oitenta noventa cem cento
duzentos duzentas trezentos trezentas quatrocentos quatrocentas quinhentos quinhentas seiscentos seiscentas setecentos
setecentas oitocentos oitocentas novecentos novecentas mil milhao milhoes bilhao bilhoes
two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion
cero cuatro siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve veinte treinta
cuarenta cincuenta sesenta ochenta cien ciento
""".split())

# ------------------------------------------------------------------ 3. palavras
#: Palavra só de letras (com apóstrofo ou hífen dentro); `\w` sem dígito e sem `_`.
_PALAVRA = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
#: Só letras, sem apóstrofo nem hífen: a unidade da conferência de alfabetos.
_LETRAS = re.compile(r"[^\W\d_]+")
#: Apóstrofo e hífen dentro da palavra: "e-mail" também vale como "email" na lista permitida (os pedaços não valem).
_JUNTA = re.compile(r"['’\-]")
#: Token com letra E (dígito ou `_`): handle sem `@`, código, placa. Vira `[termo]` sempre.
_MISTO = re.compile(r"\b(?=\w*[^\W\d_])(?=\w*[\d_])\w+\b")

#: O vocabulário PERMITIDO fixo (normalizado: minúsculas, sem acento). Português e inglês (D-J7 mede os dois), palavras
#: funcionais, verbos de comando nas formas comuns e substantivos do domínio de aparelhos e apps. Palavra de CONTEÚDO que
#: também é nome de pessoa fica de fora (rosa, clara, flor, luz, mar, sol, vitoria, graca, celeste, aurora, marco, mark e,
#: desde a reverificação de 03/10, ali, abril, cole, conte, dias, domingo, edite, more, nova, page, price): cada uma seria um
#: nome de terceiro passando. Ficam as FUNCIONAIS que coincidem com nome (do, em, uma, la, le, lo, an, mas, sim, ela) e alguns
#: verbos e substantivos de comando (post, read, close, edit, novo, segundo, durante): o risco residual é decisão do dono
#: (`docs/design/jev-golden-set.md`), e a regra da maiúscula fora do início da frase mascara o nome escrito como nome.
#: Palavra que falta aqui só custa utilidade (vira `[termo]`), nunca privacidade; o bloco final (03/10) repõe as palavras
#: de comando que antes vinham do catálogo, conferidas contra 537 nomes PT/EN/ES. Nome de app também não entra (ADR-052:
#: conhecimento de app é dado): vem do id do app e dos rótulos do registro, que quem chama passa em `vocabulario`. Numeral
#: por extenso também não (`_NUMERAIS`).
_COMUNS: Final[frozenset[str]] = frozenset("""
a o as os um uma uns umas de da do das dos em na no nas nos num numa por pelo pela pelos pelas para pra pro com sem sob
sobre entre ate apos antes depois durante desde e ou mas nem que se ao aos a la lo isso isto esse essa este esta aquele
aquela aqui la onde quando como qual quais quanto quantos quantas quem cada todo toda todos todas tudo nada algum
alguma alguns algumas outro outra outros outras mesmo mesma mais menos muito muita muitos muitas pouco pouca so apenas
tambem ainda ja agora hoje ontem amanha sempre nunca depois logo entao porque pois enquanto ja nao sim
eu tu ele ela nos vos eles elas voce voces me te se lhe lhes meu minha meus minhas teu tua seu sua seus suas nosso nossa
dele dela deles delas
primeiro primeira segundo segunda terceiro terceira ultimo ultima ultimos ultimas proximo proxima proximos proximas novo
novos novas antigo antiga recente recentes mesmo mesma
terca quarta quinta sexta sabado semana mes ano dia hora horas minuto minutos manha tarde noite
janeiro fevereiro maio junho julho agosto setembro outubro novembro dezembro
vez vezes
abra abrir abre abriu abrindo feche fechar fecha fechou entre entrar entra entrou saia sair sai saiu volte voltar volta
voltou va ir vai foi veja ver ve viu olhe olhar olha leia ler le leu procure procurar procura procurou busque buscar
busca pesquise pesquisar pesquisa encontre encontrar encontra ache achar acha toque tocar toca tocou clique clicar clica
role rolar rola rolou deslize deslizar arraste arrastar digite digitar digita escreva escrever escreve escreveu
mande mandar manda mandou envie enviar envia enviou responda responder responde respondeu comente comentar comenta
comentou curta curtir curte curtiu descurta descurtir siga seguir segue seguiu deixe deixar deixa pare parar
compartilhe compartilhar compartilha poste postar posta postou publique publicar publica publicou salve salvar salva
salvou apague apagar apaga apagou exclua excluir remova remover editar edita mude mudar muda troque trocar
copie copiar colar baixe baixar instale instalar desinstale desinstalar atualize atualizar atualiza
confira conferir verifique verificar verifica contar conta contou liste listar lista anote anotar anota
registre registrar diga dizer diz disse fale falar fala falou informe informar mostre mostrar mostra faca fazer faz fez use usar usa
tire tirar tira capture capturar grave gravar inicie iniciar comece comecar termine terminar repita repetir
aceite aceitar recuse recusar marque marcar desmarque ative ativar desative desativar ligue ligar desligue desligar
selecione selecionar escolha escolher escolhe acompanhe acompanhar monitore monitorar observe observar compare comparar
resuma resumir traduza traduzir confirme confirmar cancele cancelar espere esperar aguarde aguardar
open close enter exit go back see look read search find tap click scroll swipe type write send reply comment like
unlike follow unfollow share post publish save delete remove edit change copy paste download install uninstall update
check count list note say tell show make do use take start stop repeat accept decline select choose wait cancel
confirm summarize translate compare monitor watch
the a an of to in on at for with without from by and or but if then than this that these those it its my your his her
their our me you him them we they is are was were be been all any each every some no not yes now today first last next
new old less many much only also again
aplicativo aplicativos app apps tela telas botao botoes menu aba abas pagina paginas site sites link links perfil perfis
conta contas usuario usuarios post posts publicacao publicacoes foto fotos video videos imagem imagens story stories
storie reels reel feed feeds legenda legendas comentario comentarios curtida curtidas mensagem mensagens conversa
conversas chat chats direct notificacao notificacoes seguidor seguidores seguindo amigo amigos grupo grupos
canal canais email emails mail caixa entrada lixeira spam assunto anexo anexos rascunho rascunhos pasta pastas
arquivo arquivos documento documentos configuracao configuracoes ajuste ajustes preco precos valor valores produto
produtos loja lojas carrinho pedido pedidos busca resultado resultados item itens noticia noticias manchete manchetes
texto textos titulo titulos relatorio relatorios resumo resumos nome nomes numero numeros data datas lido lidos lida lidas nao_lido novo
aparelho aparelhos celular telefone emulador wifi rede internet bluetooth bateria som volume brilho tema modo
maps play store navegador
camera galeria agenda calendario relogio calculadora contatos
profile account message messages conversation photo photos image images caption comments likes followers following
friend friends group screen button tab settings inbox folder file files prices product products store
cart order result results item items news headline title name number date unread read
contato conectada conectado conectar conecte bom boa bons boas direta diretas direto diretos navegar navegue navega
alterar altere altera alterou identificar identifique identifica exibir exiba exibe exibida exibido listada listadas
listado listados levantar levante executar execute executa localizar localize localiza confirmando confirmado confirmada
preencher preencha preenche informado informada interagir interacao interacoes nenhum nenhuma qualquer quaisquer
disponivel disponiveis exato exata exatamente simples elogio elogios efeito externo externa acao acoes cuja cujo cujas
cujos comando comandos versao versoes captura capturas inicial principal topo grade status coleta coletar repeticao
repeticoes entrega entregar chamado chamados suporte aviso avisos cidade cidades enviando voltando segui segui-lo
digitado digitada leitura resposta respostas contact contacts replies opened latest recent
""".split())


# ------------------------------------------------------------------ normalização (o passo 0)
#: Somem depois do NFKC: marca combinante que sobrou (zalgo, risco sobre a letra) e caractere invisível de formato (ZWSP,
#: ZWJ, hífen suave, controle de direção). Assim "j̶o̶a̶n̶a̶" e "a<ZWSP>na" viram uma palavra só, julgada inteira.
_SOMEM: Final = frozenset({"Mn", "Mc", "Me", "Cf"})
#: Fim de frase: a palavra seguinte com maiúscula é começo de frase, não nome.
_FIM_DE_FRASE: Final = frozenset(".!?\n")
#: O que fica entre a palavra e o fim da frase sem mudar que ela começa a frase ("(Abra", "- Abra", "¿Viste").
_ABRE_FRASE: Final = " \t\r\f\v([{-*>•¡¿"
#: Pontuação que fica no texto (o resto que não é letra, algarismo nem espaço é símbolo). A aspa ASCII que sobrevive a
#: `_primeira_aspa_sobrando` é só o apóstrofo entre letras; os colchetes são dos marcadores.
_PONTUACAO: Final = frozenset("!#$%&()*+,-./:;<=>?@[\\]^_{|}~'’¡¿")


def sem_acento(texto: str) -> str:
    """Para comparar: casefold e sem acento (NFKD sem as marcas combinantes)."""
    decomposto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


_chave = sem_acento


def normalizar(texto: str) -> str:
    """NFKC; sem marca combinante, caractere invisível de formato nem controle que não seja espaço; todo traço como "-" e
    separador de linha ou parágrafo como quebra de linha. Letra circulada, de largura cheia, matemática ou sobrescrita vira
    a letra comum. Idempotente."""
    saida: list[str] = []
    for c in unicodedata.normalize("NFKC", texto):
        cat = unicodedata.category(c)
        if cat in _SOMEM or (cat == "Cc" and not c.isspace()):
            continue
        saida.append("-" if cat == "Pd" else "\n" if cat in ("Zl", "Zp") else c)
    return unicodedata.normalize("NFKC", "".join(saida))


def _alfabeto(c: str) -> str:
    """O sistema de escrita da letra, pelo começo do nome Unicode. Hiragana, katakana e ideograma contam como um só: a
    escrita japonesa os mistura na mesma palavra."""
    primeiro = unicodedata.name(c, "?").split(" ", 1)[0]
    return "CJK" if primeiro in ("CJK", "HIRAGANA", "KATAKANA", "KATAKANA-HIRAGANA", "IDEOGRAPHIC") else primeiro


def mistura_alfabetos(texto: str) -> bool:
    """Alguma palavra mistura sistemas de escrita (o "а" cirílico em "senhа", "Јoana")? Homóglifo é o jeito de uma palavra
    passar por outra, e não há tabela de confusíveis na biblioteca padrão: quem chama RECUSA."""
    return any(len({_alfabeto(c) for c in m.group()}) > 1 for m in _LETRAS.finditer(texto))


# ------------------------------------------------------------------ peças das duas passadas
def _primeira_aspa_sobrando(texto: str) -> int | None:
    """A posição da primeira aspa que sobrou depois de `_ASPAS` (desbalanceada, de outro sistema, pontuação de abrir ou
    fechar citação, colchete que não é ASCII), ou `None`. O apóstrofo entre duas letras ("D'Ávila", "d’água") não conta."""
    for i, c in enumerate(texto):
        if c in "'’" and 0 < i < len(texto) - 1 and texto[i - 1].isalpha() and texto[i + 1].isalpha():
            continue
        cat = unicodedata.category(c)
        if (c in _CARACTERES_DE_ASPA or cat in ("Pi", "Pf") or (cat in ("Ps", "Pe") and not c.isascii())
                or "QUOTATION" in unicodedata.name(c, "")):
            return i
    return None


def _formas(texto: str) -> str:
    """Link, e-mail, `@handle`, domínio, telefone e número viram marcador, do mais específico ao mais geral."""
    texto = _URL.sub(M_URL, texto)
    texto = _EMAIL.sub(M_EMAIL, texto)
    texto = _HANDLE.sub(M_HANDLE, texto)
    texto = _DOMINIO.sub(M_URL, texto)
    texto = _TELEFONE.sub(M_TELEFONE, texto)
    return _NUMERO.sub(M_NUMERO, texto)


def _e_simbolo(c: str) -> bool:
    return not (c.isalpha() or c.isdigit() or c.isspace() or c in _PONTUACAO)


def _simbolos(texto: str, *, recusar_colado: bool) -> str | None:
    """Cada sequência de símbolos (emoji, indicador regional, braille, letra em quadrado negativo, pontuação que não é
    ASCII) vira `[texto]`. Com `recusar_colado`, `None` se a sequência está entre duas letras ("jo❤ana"): do outro lado,
    a palavra seria lida inteira."""
    partes: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        if not _e_simbolo(texto[i]):
            partes.append(texto[i])
            i += 1
            continue
        j = i
        while j < n and _e_simbolo(texto[j]):
            j += 1
        if recusar_colado and 0 < i and j < n and texto[i - 1].isalpha() and texto[j].isalpha():
            return None
        partes.append(M_TEXTO)
        i = j
    return "".join(partes)


def _sem_marcadores(texto: str) -> str:
    for m in _MARCADORES:
        texto = texto.replace(m, " ")
    return texto


def _recusa(resto: str) -> bool:
    """O que sobrou sem os marcadores ainda tem forma que não se mascara com segurança?"""
    return bool(_RECUSA.search(resto) or _DITADO.search(resto) or _SOBRA_DIGITOS.search(resto)
                or _PARENTESES_COM_DIGITO.search(resto))


def _e_miolo(m: re.Match[str], texto: str) -> bool:
    """A palavra é o miolo de um marcador (`[termo]`), e não palavra do texto?"""
    return (m.group() in _MIOLOS and m.start() > 0 and texto[m.start() - 1] == "["
            and texto[m.end():m.end() + 1] == "]")


def _inicio_de_frase(texto: str, pos: int) -> bool:
    antes = texto[:pos].rstrip(_ABRE_FRASE)
    return not antes or antes[-1] in _FIM_DE_FRASE


def _permitida(k: str, permitidas: frozenset[str]) -> bool:
    # inteira ou sem o hífen; os pedaços NÃO ("a-na" não passa por "a" e "na")
    return k in permitidas or _JUNTA.sub("", k) in permitidas


def _juntar_termos(texto: str) -> str:
    texto = re.sub(r"\[termo\](?:[\s'’\-]*\[termo\])+", M_TERMO, texto)     # "Joana Silva" -> um só
    return re.sub(r"[ \t]{2,}", " ", texto).strip()


# ------------------------------------------------------------------ vocabulário de app
def vocabulario_de(textos: Iterable[str | None], *, minimo: int = 3) -> frozenset[str]:
    """As palavras normalizadas de NOMES DE APP (o id do app da execução e os rótulos do registro, ADR-052), para somar à
    lista permitida e isentar da regra da maiúscula. Só palavras de letras com `minimo` caracteres ou mais: id e código
    não viram vocabulário. NUNCA texto de catálogo nem de persona (reverificação de 03/10): o nome de fluxo legado traz o
    destino de um comando antigo."""
    saida: set[str] = set()
    for t in textos:
        for m in _PALAVRA.finditer(normalizar(t) if isinstance(t, str) else ""):
            k = _chave(m.group())
            if len(k) >= minimo:
                saida.add(k)
    return frozenset(saida)


# ------------------------------------------------------------------ a C3: o comando
def remover_entidades(texto: str, *, vocabulario: Iterable[str] = ()) -> str | None:
    """O texto com toda palavra fora do vocabulário permitido trocada por `[termo]`, ou `None` (falha fechada).

    `vocabulario` são palavras extras permitidas, e isentas da regra da maiúscula: nomes de app (`vocabulario_de` sobre o
    id do app e os rótulos do registro). Devolve `None` quando: a entrada não é texto; alguma palavra mistura alfabetos;
    sobra aspa ou símbolo colado entre letras; sobra forma que não se mascara com segurança (endereço, documento, e-mail
    por extenso ou ofuscado, `@`, `://`, numerais ditados); ou a proporção de palavras desconhecidas passa do limiar.
    Idempotente."""
    if not isinstance(texto, str):
        return None
    texto = normalizar(texto)
    if mistura_alfabetos(texto):
        return None
    extras = frozenset(_chave(v) for v in vocabulario)
    permitidas = _COMUNS | extras
    trocado = _ASPAS.sub(M_TEXTO, texto)
    if _primeira_aspa_sobrando(trocado) is not None:
        return None
    trocado = _simbolos(_formas(trocado), recusar_colado=True)
    if trocado is None or _recusa(_sem_marcadores(trocado)):
        return None
    total = desconhecidas = numerais = 0

    def _misto(_m: re.Match[str]) -> str:
        nonlocal total, desconhecidas
        total += 1
        desconhecidas += 1
        return M_TERMO

    trocado = _MISTO.sub(_misto, trocado)
    # A regra da maiúscula só vale em texto que tem minúscula (fora dos marcadores): o comando todo em caixa alta não diz
    # nada pela caixa, e a passada seguinte, sobre a saída com marcadores, tem de decidir igual (idempotência).
    maiuscula_diz = any(c.islower() for c in _sem_marcadores(trocado))

    def _palavra(m: re.Match[str]) -> str:
        nonlocal total, desconhecidas, numerais
        p = m.group()
        if _e_miolo(m, trocado):
            return p
        k = _chave(p)
        if k in _NUMERAIS:
            numerais += 1
            return M_NUMERO
        total += 1
        if _permitida(k, extras):
            return p
        if maiuscula_diz and p[0].isupper() and not _inicio_de_frase(trocado, m.start()):
            desconhecidas += 1                       # "send a message to Uma", "mande para Do Van Minh"
            return M_TERMO
        if _permitida(k, permitidas):
            return p
        desconhecidas += 1
        return M_TERMO

    trocado = _juntar_termos(_PALAVRA.sub(_palavra, trocado))
    if numerais >= 2:
        return None
    if total and (desconhecidas / total > LIMIAR_DESCONHECIDAS or desconhecidas > MAX_DESCONHECIDAS):
        return None
    return trocado


# ------------------------------------------------------------------ a C2: o texto do catálogo nas opções da R2
def mascarar_catalogo(texto: str, *, isentas: Iterable[str] = ()) -> str:
    """O texto do catálogo do dono (C2: nome e descrição de habilidade ou fluxo) para ir como descrição de opção da R2.

    Não recusa (a opção precisa existir) e não tem lista de permissão (C2 é liberado), mas o nome de fluxo legado é o
    resumo de um comando antigo, com o destino dentro (reverificação de 03/10: `@handles` e trechos de legenda iam em todo
    pedido). Passam as mesmas máscaras de forma (aspas, link, e-mail, handle, domínio, telefone, número e símbolo); o
    trecho a partir de uma aspa que sobra é cortado (a legenda que o corte em 120 caracteres deixou aberta); numeral vira
    `[numero]`; e vira `[termo]` a palavra com maiúscula fora do início da frase que não seja palavra comum nem nome de
    app (`isentas`): o nome de terceiro escrito como nome. Texto com alfabetos misturados, endereço, documento ou e-mail por extenso
    sai vazio: quem chama põe um texto fixo."""
    if not isinstance(texto, str):
        return ""
    t = normalizar(texto)
    if mistura_alfabetos(t):
        return ""
    t = _ASPAS.sub(M_TEXTO, t)
    corte = _primeira_aspa_sobrando(t)
    if corte is not None:
        t = t[:corte]
    t = _simbolos(_formas(t), recusar_colado=False) or ""
    if _recusa(_sem_marcadores(t)):
        return ""
    t = _MISTO.sub(M_TERMO, t)
    comuns = _COMUNS | frozenset(_chave(v) for v in isentas)
    maiuscula_diz = any(c.islower() for c in _sem_marcadores(t))

    def _palavra(m: re.Match[str]) -> str:
        p = m.group()
        if _e_miolo(m, t):
            return p
        k = _chave(p)
        if k in _NUMERAIS:
            return M_NUMERO
        if (maiuscula_diz and p[0].isupper() and not _inicio_de_frase(t, m.start())
                and not _permitida(k, comuns)):
            return M_TERMO
        return p

    return _juntar_termos(_PALAVRA.sub(_palavra, t))


__all__ = ["LIMIAR_DESCONHECIDAS", "MAX_DESCONHECIDAS", "M_EMAIL", "M_HANDLE", "M_NUMERO", "M_TELEFONE", "M_TERMO",
           "M_TEXTO", "M_URL", "mascarar_catalogo", "mistura_alfabetos", "normalizar", "remover_entidades",
           "sem_acento", "vocabulario_de"]
