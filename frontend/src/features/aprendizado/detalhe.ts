/**
 * O detalhe rico do item do Livro (30.16, `docs/design/aprendizado-vivo.md` §11.2): tradução para português do que o
 * backend já mediu e montou (saúde, conteúdo, versão, relações). Aqui só se apresenta: taxa, tendência, ordem e saúde
 * são do backend (`domain/saude.py`, `conteudo.py`, `versao.py`, `relacoes.py`) e nada é recalculado.
 *
 * Duas regras de leitura: valor desconhecido aparece como "sem dado" (nunca como zero) e valor de parâmetro nunca
 * existe aqui (o backend manda só os NOMES; a regra do segredo é dele).
 */
import { Archive, CircleAlert, CircleCheck, CircleDashed, CircleHelp, CirclePause, CircleOff, TrendingDown, type LucideIcon } from 'lucide-react';
import { formatInt, formatPercent } from '../../lib/format';
import type { Tone } from '../../lib/status';
import { formatQuando } from '../../lib/time';
import {
  type AlvoDaAcao, type DimensaoDeSaude, type EntradaDoLivro, type EstadoDeVersao, type MotivoDeSaude, type RelacaoDoItem, type RotuloDeSaude,
  type SaudeDoItem, type TipoDeRelacao, isLivroKind, type LivroKind,
} from './model';

export const SEM_DADO = 'sem dado';

// ---------------------------------------------------------------- saúde

interface MetaDeSaude {
  label: string;
  tone: Tone;
  icon: LucideIcon;
  description: string;
}

export const SAUDE_META: Record<RotuloDeSaude, MetaDeSaude> = {
  saudavel: { label: 'Saudável', tone: 'success', icon: CircleCheck, description: 'Usado há pouco, com amostra e acerto suficientes e sem contestação recente.' },
  degradando: { label: 'Degradando', tone: 'danger', icon: TrendingDown, description: 'Publicado, mas falhando ou contestado: vale olhar.' },
  obsoleto_provavel: { label: 'Provavelmente obsoleto', tone: 'warning', icon: Archive, description: 'Publicado, mas algo indica que perdeu a razão de existir (substituto, versão, catálogo). Nada é desligado sozinho por isso.' },
  em_prova: { label: 'Em prova', tone: 'info', icon: CircleDashed, description: 'Ainda não valeu: espera repetir ou uma decisão.' },
  pouca_amostra: { label: 'Pouca amostra', tone: 'info', icon: CircleDashed, description: 'Usado poucas vezes: cedo para julgar.' },
  sem_evidencia: { label: 'Sem evidência', tone: 'warning', icon: CircleAlert, description: 'Publicado há tempo e nunca usado.' },
  parado: { label: 'Parado', tone: 'warning', icon: CirclePause, description: 'Já foi usado, mas está sem uso há mais que o limite.' },
  inativo: { label: 'Inativo', tone: 'muted', icon: CircleOff, description: 'Aposentado ou desligado: fora de circulação.' },
  indeterminado: { label: 'Sem medida', tone: 'muted', icon: CircleHelp, description: 'Falta dado para decidir; nunca é contado como saudável.' },
};

/** Rótulo de uma versão mais nova do backend (fora do vocabulário): aparece como veio, sem tom. */
export function metaDeSaude(rotulo: string | null | undefined): MetaDeSaude | null {
  if (!rotulo) return null;
  return (SAUDE_META as Record<string, MetaDeSaude | undefined>)[rotulo]
    ?? { label: rotulo, tone: 'neutral', icon: CircleHelp, description: 'Rótulo que este painel ainda não conhece.' };
}

const pct = (v: number | string | null): string => (typeof v === 'number' ? formatPercent(Math.round(v * 100)) : SEM_DADO);
const num = (v: number | string | null): string => (typeof v === 'number' ? formatInt(v) : v === null ? SEM_DADO : v);
const dias = (v: number | string | null): string => (typeof v === 'number' ? `${formatInt(v)} ${v === 1 ? 'dia' : 'dias'}` : SEM_DADO);
const com = (frase: string, detalhe: string | null) => (detalhe ? `${frase} (${detalhe})` : frase);

/** Os acertos e a amostra por trás da taxa de eficácia, da dimensão `eficacia` (valor = taxa, amostra = acertos + falhas).
 *  `null` sem a dimensão medida ou quando a taxa não é acertos/amostra inteiros (nunca arredonda para caber). */
function acertosDaEficacia(taxa: number | string | null, saude?: Pick<SaudeDoItem, 'dimensoes'> | null): { acertos: number; amostra: number } | null {
  const dim = saude?.dimensoes.find((d) => d.nome === 'eficacia' && d.estado === 'medida');
  if (typeof taxa !== 'number' || !dim || typeof dim.amostra !== 'number' || dim.amostra <= 0) return null;
  const acertos = Math.round(taxa * dim.amostra);
  return Math.abs(acertos / dim.amostra - taxa) < 0.00006 ? { acertos, amostra: dim.amostra } : null;
}

/**
 * Um motivo da saúde como frase: o fato medido e o limiar que ele cruzou (ou não). O vocabulário é fechado no
 * backend (`CodigoDoMotivo`); código novo aparece como veio, com o que ele mediu, em vez de sumir.
 */
export function textoDoMotivo(m: MotivoDeSaude, saude?: Pick<SaudeDoItem, 'dimensoes'> | null): string {
  const { valor, limite, detalhe } = m;
  switch (m.codigo) {
    case 'desligado': return com('Foi desligado', detalhe);
    case 'aposentado': return com('Foi aposentado', detalhe);
    case 'aguarda_repeticao': return 'Ainda não se repetiu o bastante para valer';
    case 'aguarda_o_dono': return 'Espera a decisão do dono';
    case 'validado_aguarda_publicacao': return 'Validado; falta ser publicado';
    case 'falhas_seguidas': return `${num(valor)} falhas seguidas (o limite é ${num(limite)})`;
    case 'eficacia_abaixo_do_minimo': {
      // 30.44: "3 de 5 deram certo (60%), abaixo de 80%", dos números que a saúde já traz: a taxa é acertos sobre a
      // AMOSTRA da dimensão (a favor + contra), e não sobre os usos do contador. Sem a dimensão (ou se não fechar), o texto antigo.
      const contagem = acertosDaEficacia(valor, saude);
      return contagem
        ? `${contagem.acertos} de ${contagem.amostra} deram certo (${pct(valor)}), abaixo de ${pct(limite)}`
        : com(`Acerta ${pct(valor)} das vezes, abaixo do mínimo de ${pct(limite)}`, detalhe);
    }
    case 'contestado_recentemente': return com(`${num(valor)} contestação(ões) recente(s)`, detalhe);
    // O mesmo texto no fluxo e na receita: o fato é o mesmo e o rótulo também (`sem_evidencia`). `fluxo_nunca_casado`
    // fica só para o backend anterior, que ainda o manda.
    case 'nunca_usado':
    case 'fluxo_nunca_casado': return `Nunca usado desde que foi publicado, há ${dias(valor)} (prazo: ${dias(limite)})`;
    case 'sem_uso_recente': return `Último uso há ${dias(valor)} (limite: ${dias(limite)})`;
    case 'amostra_pequena': return `Só ${num(valor)} usos; precisa de ${num(limite)} para julgar`;
    case 'usado_recentemente': return `Usado há ${dias(valor)} (limite: ${dias(limite)})`;
    case 'amostra_suficiente': return `${num(valor)} usos (mínimo: ${num(limite)})`;
    case 'eficacia_acima_do_minimo': return `Acerta ${pct(valor)} das vezes (mínimo: ${pct(limite)})`;
    case 'sem_contestacao_recente': return com('Sem contestação', detalhe);
    case 'uso_desconhecido': return 'Sem dado: não se sabe quantas vezes nem quando foi usado';
    case 'idade_desconhecida': return 'Sem dado: não se sabe desde quando está publicado';
    case 'eficacia_desconhecida': return 'Sem dado: nenhuma tentativa medida ainda';
    case 'contestacao_desconhecida': return 'Sem dado: as contestações não puderam ser lidas';
    case 'substituta_viva': return `Há uma versão mais nova em uso (${String(valor ?? '')})`;
    case 'versao_fora_do_parque': return `Só existe na versão ${String(valor ?? '')} do app, que nenhum aparelho tem mais`;
    case 'versao_viva_sem_reproducao': return `Nunca reproduzido na versão do app em uso (${String(valor ?? '')})`;
    case 'efeito_sem_respaldo_no_catalogo':
      return `Age fora da máquina, mas o catálogo atual do app não permite esse efeito (${String(valor ?? '*')})`;
    case 'absorvida': return 'Já faz parte do conhecimento declarado do app';
    case 'estado_desconhecido': return 'Sem dado: o estado do item não é conhecido';
    default: return com(`${m.codigo}${valor !== null ? `: ${num(valor)}` : ''}${limite !== null ? ` (limite ${num(limite)})` : ''}`, detalhe);
  }
}

const DIMENSAO_LABEL: Record<string, string> = {
  uso: 'Uso', eficacia: 'Eficácia', base_de_evidencia: 'Base medida', frescor: 'Frescor', versao: 'Versão do app',
  contestacao: 'Contestação', intervencao_humana: 'Intervenção humana',
};

export function rotuloDaDimensao(nome: string): string {
  return DIMENSAO_LABEL[nome] ?? nome;
}

/** O valor de uma dimensão em palavras; `desconhecida` (ou valor nulo) é "sem dado", nunca 0. */
export function valorDaDimensao(d: DimensaoDeSaude): string {
  if (d.estado === 'desconhecida' || d.valor === null) return SEM_DADO;
  const amostra = d.amostra !== null ? ` (em ${formatInt(d.amostra)})` : '';
  switch (d.nome) {
    case 'uso': return `${num(d.valor)} usos`;
    case 'eficacia': return `${pct(d.valor)}${amostra}`;
    // Na receita a base são as reproduções (deram certo + falharam), não as evidências registradas no Livro.
    case 'base_de_evidencia': return `${num(d.valor)} registros (reproduções e evidências)`;
    case 'frescor': return `${dias(d.valor)} desde o último uso (ou desde a criação)`;
    case 'contestacao': return `${num(d.valor)} nos últimos dias`;
    default: return num(d.valor);
  }
}

/** A saúde só se mostra com o que ela tem (a memória e o backend antigo vêm sem ela). */
export function saudeDoItem(e: Pick<EntradaDoLivro, 'saude'>): SaudeDoItem | null {
  return e.saude ?? null;
}

// ---------------------------------------------------------------- versão

interface MetaDeVersao {
  label: string;
  tone: Tone;
  description: string;
}

export const VERSAO_META: Record<EstadoDeVersao, MetaDeVersao> = {
  independente: { label: 'Não depende da versão do app', tone: 'muted', description: 'Este tipo de item vale em qualquer versão.' },
  comprovado: { label: 'Comprovado', tone: 'success', description: 'Funcionou nesta versão do app, que está viva no parque.' },
  nao_testado: { label: 'Não testado', tone: 'warning', description: 'A versão está no parque, mas nenhuma receita desta etapa foi gravada nela.' },
  em_prova: { label: 'Em prova', tone: 'info', description: 'Ativo, mas ainda sem uso que o comprove nesta versão.' },
  falhando: { label: 'Falhando', tone: 'danger', description: 'Está falhando nesta versão do app.' },
  incompativel: { label: 'Incompatível', tone: 'danger', description: 'Funcionava numa versão anterior e quebrou nesta.' },
  superseded: { label: 'Trocada', tone: 'muted', description: 'Foi trocada por uma receita mais nova da mesma etapa.' },
  versao_aposentada: { label: 'Versão aposentada', tone: 'muted', description: 'Nenhum aparelho usa mais esta versão do app (não é falha).' },
  desconhecido: { label: 'Sem dado', tone: 'muted', description: 'Não se sabe: sem pacote, sem versão ou sem aparelho observado.' },
};

export function metaDeVersao(estado: string | null | undefined): MetaDeVersao {
  return (VERSAO_META as Record<string, MetaDeVersao | undefined>)[estado ?? '']
    ?? { label: estado || SEM_DADO, tone: 'neutral', description: 'Estado que este painel ainda não conhece.' };
}

export function textoDeAparelhos(n: number): string {
  return n === 1 ? '1 aparelho' : `${formatInt(n)} aparelhos`;
}

// ---------------------------------------------------------------- relações

const RELACAO_LABEL: Record<TipoDeRelacao, string> = {
  substitui: 'Substitui', substituida_por: 'Substituída por', derivado_de: 'Derivado de', reaprende: 'Reaprende',
  reaprendida_por: 'Reaprendida por', absorvida: 'Absorvida por', contradiz: 'Contradiz',
};

export function rotuloDaRelacao(t: string): string {
  return (RELACAO_LABEL as Record<string, string | undefined>)[t] ?? t;
}

/**
 * Para onde a relação aponta no Livro: o item que o `kind` e o `ref` nomeiam. `regra_declarada` e `commit` (a
 * absorção) não são itens do Livro e ficam sem destino: viram texto.
 */
export function destinoDaRelacao(r: Pick<RelacaoDoItem, 'kind' | 'ref'>): { kind: LivroKind; ref: string } | null {
  return isLivroKind(r.kind) && r.ref ? { kind: r.kind, ref: r.ref } : null;
}

/** O que a relação "absorvida" diz quando o alvo não é um item (a regra declarada ou o commit). */
export function textoDoAlvoSemItem(r: Pick<RelacaoDoItem, 'kind' | 'ref' | 'rotulo'>): string {
  if (r.kind === 'regra_declarada') return `a regra declarada "${r.ref}"`;
  if (r.kind === 'commit') return `o commit ${r.ref}`;
  return r.rotulo ?? `${r.kind} ${r.ref}`;
}

// ---------------------------------------------------------------- conteúdo

const FERRAMENTA_LABEL: Record<string, string> = {
  tap: 'Toca', long_press: 'Pressiona e segura', drag: 'Arrasta', scroll: 'Rola a tela', type_text: 'Digita um texto',
  type_secret: 'Digita um dado sigiloso', press_back: 'Volta', press_home: 'Vai para o início', open_app: 'Abre o app',
  open_url: 'Abre um endereço', wait_for: 'Espera algo aparecer', verify_state: 'Confere o estado', collect_list: 'Coleta uma lista',
  read_value: 'Lê um valor', find_element: 'Procura um elemento', observe_screen: 'Olha a tela',
};

export function rotuloDaFerramenta(f: string | null): string {
  return f ? FERRAMENTA_LABEL[f] ?? f : 'Ação sem ferramenta';
}

const SELETOR_LABEL: Record<string, string> = { rid: 'id', texto: 'texto', desc: 'descrição' };
const TIPO_DE_SELETOR_LABEL: Record<string, string> = {
  'rid+text': 'id e texto', 'rid+desc': 'id e descrição', rid: 'id', desc: 'descrição', text: 'texto',
};

export function rotuloDoSeletor(tipo: string): string {
  return TIPO_DE_SELETOR_LABEL[tipo] ?? tipo;
}

export function rotuloDoCampoDoSeletor(campo: string): string {
  return SELETOR_LABEL[campo] ?? campo;
}

/**
 * O alvo de uma ação em palavras (UX do deploy 8: o detalhe abria nos seletores crus): o texto ou a descrição do
 * primeiro seletor que tiver, entre aspas; sem nenhum, o nome curto do id ("entry" de "com.x:id/entry"). Nulo sem
 * seletor. Os seletores inteiros seguem em "como o encontra", para quem desenvolve.
 */
export function alvoLegivel(alvo: readonly AlvoDaAcao[]): string | null {
  for (const s of alvo) {
    const t = (s.texto ?? s.desc ?? '').trim();
    if (t) return `“${t}”`;
  }
  const rid = alvo.find((s) => typeof s.rid === 'string' && s.rid.trim())?.rid;
  return rid ? `o elemento ${rid.split(':id/').pop()}` : null;
}

/** A variante da tela em que a receita foi gravada ("en-US/xhdpi": idioma/densidade) em palavras; outro formato
 *  segue como veio. */
export function textoDaVariante(v: string | null | undefined): string | null {
  if (!v) return null;
  const partes = v.split('/');
  return partes.length === 2 && partes[0] && partes[1] ? `idioma ${partes[0]}, tela ${partes[1]}` : v;
}

const RE_RUN_ID = /^r-(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})-[0-9a-f]+$/;

/** A execução como o painel fala: "execução de 24/09 08:48" (o id `r-20260924114815-c14258` traz a hora UTC); fora
 *  do formato, "execução <id>". O id cru vai no `title` do link. */
export function rotuloDaExecucao(runId: string, agoraMs?: number): string {
  const m = RE_RUN_ID.exec(runId);
  return m ? `execução de ${formatQuando(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`, agoraMs)}` : `execução ${runId}`;
}

const ALVO_DA_LICAO: Record<string, string> = { parametro: 'o parâmetro', texto: 'o texto', elemento: 'o elemento' };

export function rotuloDoAlvoDaLicao(tipo: string | null): string {
  return tipo ? ALVO_DA_LICAO[tipo] ?? tipo : 'alvo';
}

/** O status NATIVO da receita (`recipes.status`) em português; status que este painel não conhece fica como veio. */
const STATUS_DA_RECEITA: Record<string, string> = {
  active: 'ativa', candidate: 'candidata', quarantined: 'em quarentena', superseded: 'trocada', disabled: 'desligada',
};

export function rotuloDoStatusDaReceita(s: string): string {
  return STATUS_DA_RECEITA[s] ?? s;
}
