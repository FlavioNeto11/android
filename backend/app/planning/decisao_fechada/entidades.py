"""Remoção de entidades por LISTA DE PERMISSÃO, que falha fechada, para a C3 da intenção (item 31.9, ADR-069 item 4).

A C3 é o comando do dono depois de `sem_destinos` e de `redact`: texto livre, que pode trazer nome de terceiro, `@handle`,
e-mail, número, telefone, endereço e link. A regra do dono é "C3 só depois de remoção de entidades que FALHA FECHADA". Uma
lista de BLOQUEIO (achar o nome e trocá-lo) falha aberta: o nome em minúsculas, o primeiro termo da frase, o handle sem
`@` e o nome fora do Latin-1 passam (revisão independente de 02/10, `.claude/handoffs/revisao-31-9.md`). Por isso aqui é
o contrário:

1. o que tem forma conhecida vira marcador fixo: o que está entre aspas (`[texto]`: é o que a pessoa manda escrever),
   link, e-mail, `@handle`, domínio, telefone, número de 3 dígitos ou mais;
2. o que não tem como ser mascarado com segurança RECUSA o texto inteiro (`None`): endereço (rua, avenida...), e-mail
   escrito por extenso ou ofuscado (`arroba`, `(at)`, `ponto com`), sobra de `@`, `://` ou dígitos;
3. TODA palavra que sobra só fica se estiver no vocabulário PERMITIDO: palavras funcionais e de comando (`_COMUNS`, abaixo,
   sem nenhuma palavra que também seja nome de pessoa) e os termos do catálogo do dono (C2), que quem chama passa. O resto
   vira `[termo]`, sem olhar caixa nem posição: "joana curtiu isso", "Joana: abra o app", "send a message to john" e
   "siga joana_silva99" saem com `[termo]` no lugar do nome. Token com dígito ou `_` misturado a letra também vira `[termo]`;
4. se a proporção de palavras trocadas passar de `LIMIAR_DESCONHECIDAS` (ou forem mais de `MAX_DESCONHECIDAS`), o texto
   inteiro é recusado: o que sobra é sobretudo máscara, mede pouco e a forma da frase já diz demais.

A caixa do texto não importa (o `TargetExtractor` pode devolver o comando todo em minúsculas, revisão, achado 3): a
comparação é por palavra normalizada (casefold, sem acento). Credencial e 2FA (C7) NÃO são tratados aqui: o consumidor
recusa o pedido inteiro antes (`intencao.py`).

Função pura: sem banco, sem rede, sem configuração.
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

#: Acima desta fração de palavras desconhecidas (trocadas por `[termo]`), o texto inteiro é recusado.
LIMIAR_DESCONHECIDAS: Final = 0.5
#: E acima deste número absoluto, também (um comando longo cheio de nomes não sai "com metade mascarada").
MAX_DESCONHECIDAS: Final = 6

# ------------------------------------------------------------------ 1. troca por forma (do mais específico ao mais geral)
#: Aspas simples ASCII e crase só fora de palavra: o apóstrofo de "D'Ávila" não abre trecho.
_ASPAS = re.compile(r'"[^"]*"|“[^”]*”|«[^»]*»|‘[^’]*’' r"|(?<!\w)'[^'\n]*'(?!\w)|`[^`]*`")
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*")
_HANDLE = re.compile(r"@\w[\w.]*")
_DOMINIO = re.compile(r"(?i)\b[\w\-]+(?:\.[\w\-]+)*\.[a-z]{2,}(?:/\S*)?\b")
_TELEFONE = re.compile(r"(?:\+?\d{1,3}[\s.\-])?\(?\d{2}\)?[\s.\-]?\d{4,5}[\s.\-]?\d{4}")
#: Qualquer número (também quebrado por UM separador entre dígitos: "12 34 56", "1.234", "11-98"). Até "pedido 12" é
#: identificador; a contagem ("curta 25 posts") perde pouco como `[numero]`.
_NUMERO = re.compile(r"\d(?:[\s.\-/]?\d)*")
#: Três ou mais algarismos por extenso em sequência ("nove nove oito sete"): é telefone ou documento ditado. Recusa.
_DITADO = re.compile(r"(?i)\b(?:(?:zero|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|meia|sete|oito|nove|one|two|three|four"
                     r"|five|six|seven|eight|nine)\W+){2,}(?:zero|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|meia|sete"
                     r"|oito|nove|one|two|three|four|five|six|seven|eight|nine)\b")

# ------------------------------------------------------------------ 2. recusa (não há máscara segura)
_RECUSA = re.compile(
    r"@|://|(?i:\bwww\b)|(?i:\barroba\b)|(?i:\bponto\s*com\b)|(?i:\bdot\s*com\b)"
    r"|(?i:[(\[]\s*(?:at|dot|arroba|ponto)\s*[)\]])"
    # endereço: o logradouro seguido de qualquer termo (o nome da rua é dado de lugar de uma pessoa)
    r"|(?i:\b(?:rua|r\.|avenida|av\.?|travessa|tv\.|alameda|al\.|pra[çc]a|rodovia|estrada|largo|viela|cep|bairro"
    r"|apartamento|apto|bloco|condom[ií]nio)\b)"
    r"|(?i:\b(?:street|st\.|avenue|ave\.|road|rd\.|zip)\b)")
_SOBRA_DIGITOS = re.compile(r"\d\D{0,2}\d\D{0,2}\d")
_PARENTESES_COM_DIGITO = re.compile(r"\(\s*\d")

# ------------------------------------------------------------------ 3. palavras
#: Palavra só de letras (com apóstrofo ou hífen dentro); `\w` sem dígito e sem `_`.
_PALAVRA = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
#: Token com letra E (dígito ou `_`): handle sem `@`, código, placa. Vira `[termo]` sempre.
_MISTO = re.compile(r"\b(?=\w*[^\W\d_])(?=\w*[\d_])\w+\b")

#: O vocabulário PERMITIDO fixo (normalizado: minúsculas, sem acento). Português e inglês (D-J7 mede os dois), palavras
#: funcionais, verbos de comando nas formas comuns e substantivos do domínio de aparelhos e apps. NENHUMA palavra que
#: também seja nome de pessoa (rosa, clara, flor, luz, mar, sol, vitoria, graca, celeste, aurora, marco, mark, ...): cada uma seria um
#: nome de terceiro passando. Palavra que falta aqui só custa utilidade (vira `[termo]`), nunca privacidade. Nome de app
#: também não entra (ADR-052: conhecimento de app é dado): vem do id do app e do catálogo, por `vocabulario_de`.
_COMUNS: Final[frozenset[str]] = frozenset("""
a o as os um uma uns umas de da do das dos em na no nas nos num numa por pelo pela pelos pelas para pra pro com sem sob
sobre entre ate apos antes depois durante desde e ou mas nem que se ao aos a la lo isso isto esse essa este esta aquele
aquela aqui ali la onde quando como qual quais quanto quantos quantas quem cada todo toda todos todas tudo nada algum
alguma alguns algumas outro outra outros outras mesmo mesma mais menos muito muita muitos muitas pouco pouca so apenas
tambem ainda ja agora hoje ontem amanha sempre nunca depois logo entao porque pois enquanto ja nao sim
eu tu ele ela nos vos eles elas voce voces me te se lhe lhes meu minha meus minhas teu tua seu sua seus suas nosso nossa
dele dela deles delas
primeiro primeira segundo segunda terceiro terceira ultimo ultima ultimos ultimas proximo proxima proximos proximas novo
nova novos novas antigo antiga recente recentes mesmo mesma
domingo terca quarta quinta sexta sabado semana mes ano dia dias hora horas minuto minutos manha tarde noite
janeiro fevereiro abril maio junho julho agosto setembro outubro novembro dezembro
um dois tres quatro cinco seis sete oito nove dez vez vezes
abra abrir abre abriu abrindo feche fechar fecha fechou entre entrar entra entrou saia sair sai saiu volte voltar volta
voltou va ir vai foi veja ver ve viu olhe olhar olha leia ler le leu procure procurar procura procurou busque buscar
busca pesquise pesquisar pesquisa encontre encontrar encontra ache achar acha toque tocar toca tocou clique clicar clica
role rolar rola rolou deslize deslizar arraste arrastar digite digitar digita escreva escrever escreve escreveu
mande mandar manda mandou envie enviar envia enviou responda responder responde respondeu comente comentar comenta
comentou curta curtir curte curtiu descurta descurtir siga seguir segue seguiu deixe deixar deixa pare parar
compartilhe compartilhar compartilha poste postar posta postou publique publicar publica publicou salve salvar salva
salvou apague apagar apaga apagou exclua excluir remova remover edite editar edita mude mudar muda troque trocar
copie copiar cole colar baixe baixar instale instalar desinstale desinstalar atualize atualizar atualiza
confira conferir verifique verificar verifica conte contar conta contou liste listar lista anote anotar anota
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
new old more less many much only also again
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
friend friends group page screen button tab settings inbox folder file files price prices product products store
cart order result results item items news headline title name number date unread read
""".split())


def _chave(palavra: str) -> str:
    """Normaliza para comparar: casefold e sem acento (NFKD sem as marcas combinantes)."""
    decomposto = unicodedata.normalize("NFKD", palavra.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def vocabulario_de(textos: Iterable[str]) -> frozenset[str]:
    """As palavras normalizadas de textos do catálogo do dono (C2: nome e descrição de habilidade, fluxo, app), para somar
    à lista permitida. Só palavras de letras com 3 ou mais caracteres: id e código não viram vocabulário."""
    saida: set[str] = set()
    for t in textos:
        for m in _PALAVRA.finditer(t or ""):
            k = _chave(m.group())
            if len(k) >= 3:
                saida.add(k)
    return frozenset(saida)


def _sem_marcadores(texto: str) -> str:
    for m in _MARCADORES:
        texto = texto.replace(m, " ")
    return texto


def remover_entidades(texto: str, *, vocabulario: Iterable[str] = ()) -> str | None:
    """O texto com toda palavra fora do vocabulário permitido trocada por `[termo]`, ou `None` (falha fechada).

    `vocabulario` são palavras extras permitidas (normalizadas; use `vocabulario_de` sobre o catálogo do dono). Devolve
    `None` quando: a entrada não é texto; sobra forma que não se mascara com segurança (endereço, e-mail por extenso ou
    ofuscado, `@`, `://`, dígitos); ou a proporção de palavras desconhecidas passa do limiar. Idempotente."""
    if not isinstance(texto, str):
        return None
    permitidas = _COMUNS | frozenset(_chave(v) for v in vocabulario)
    trocado = _ASPAS.sub(M_TEXTO, texto)
    trocado = _URL.sub(M_URL, trocado)
    trocado = _EMAIL.sub(M_EMAIL, trocado)
    trocado = _HANDLE.sub(M_HANDLE, trocado)
    trocado = _DOMINIO.sub(M_URL, trocado)
    trocado = _TELEFONE.sub(M_TELEFONE, trocado)
    trocado = _NUMERO.sub(M_NUMERO, trocado)
    resto = _sem_marcadores(trocado)
    if (_RECUSA.search(resto) or _DITADO.search(resto) or _SOBRA_DIGITOS.search(resto)
            or _PARENTESES_COM_DIGITO.search(resto)):
        return None
    total = desconhecidas = 0

    def _misto(_m: re.Match[str]) -> str:
        nonlocal total, desconhecidas
        total += 1
        desconhecidas += 1
        return M_TERMO

    trocado = _MISTO.sub(_misto, trocado)

    def _palavra(m: re.Match[str]) -> str:
        nonlocal total, desconhecidas
        p = m.group()
        if p in ("link", "email", "usuario", "telefone", "numero", "termo", "texto") and (
                m.start() > 0 and trocado[m.start() - 1] == "[" and trocado[m.end():m.end() + 1] == "]"):
            return p                                     # o miolo de um marcador
        total += 1
        if _chave(p) in permitidas:
            return p
        desconhecidas += 1
        return M_TERMO

    trocado = _PALAVRA.sub(_palavra, trocado)
    trocado = re.sub(r"\[termo\](?:[\s'’\-]*\[termo\])+", M_TERMO, trocado)      # "Joana Silva" -> um só
    trocado = re.sub(r"[ \t]{2,}", " ", trocado).strip()
    if total and (desconhecidas / total > LIMIAR_DESCONHECIDAS or desconhecidas > MAX_DESCONHECIDAS):
        return None
    return trocado


__all__ = ["LIMIAR_DESCONHECIDAS", "MAX_DESCONHECIDAS", "M_EMAIL", "M_HANDLE", "M_NUMERO", "M_TELEFONE", "M_TERMO",
           "M_TEXTO", "M_URL", "remover_entidades", "vocabulario_de"]
