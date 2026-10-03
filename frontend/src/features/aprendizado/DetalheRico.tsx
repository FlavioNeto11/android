import { Zap } from 'lucide-react';
import { createContext, useContext, useId, useState, type ReactNode } from 'react';
import { hintForError, toApiError } from '../../api/client';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { formatInt } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, formatQuando } from '../../lib/time';
import { apiAprendizado } from './api';
import { abrirApp } from './apps';
import { SecaoDoParecer } from './ParecerDaIA';
import {
  SEM_DADO, destinoDaRelacao, metaDeSaude, metaDeVersao, rotuloDaDimensao, rotuloDaFerramenta, rotuloDaRelacao,
  rotuloDoAlvoDaLicao, rotuloDoCampoDoSeletor, rotuloDoSeletor, rotuloDoStatusDaReceita, textoDeAparelhos, textoDoAlvoSemItem, textoDoMotivo,
  valorDaDimensao,
} from './detalhe';
import {
  type AcaoDaReceita, type ConteudoDaHabilidade, type ConteudoDaLicao, type ConteudoDaReceita, type ConteudoDaTela,
  type ConteudoDoFluxo, type ConteudoDoItem, type DetalheDoLivro, type EntradaDoLivro, type EvidenciaDoLivro,
  type LivroKind, type OrigemDaReceita, type RelacaoDoItem, type SaudeDoItem, type TransicaoDoLivro, type VersaoDoItem,
  type VizinhaDaReceita, ORIGEM_LABEL, acoesDoItem, nomearCapabilityNoTexto, porQueOSistemaNaoPublica, rotuloDoEstado,
  rotuloDoKind,
} from './model';
import styles from './Aprendizado.module.css';

/** O link do item no próprio Livro (`#/aprendizado?aba=aprendido&item=<kind>:<ref>`): um link de verdade, que abre em outra guia. */
export const hrefDoItem = (kind: LivroKind, ref: string): string =>
  hashDe('aprendizado', { query: { aba: 'aprendido', item: `${kind}:${ref}` } });

/** A execução (e a etapa, quando se sabe) onde algo foi visto: link de verdade para o relatório. */
const hrefDaExecucao = (runId: string): string => hashDe('execucoes', { segmentos: [runId] });

/** O prefixo de ids de UM detalhe (`useId`): vários detalhes abertos na mesma lista não repetem o id do título. */
const PrefixoDeIds = createContext('det');

function Secao({ slug, titulo, children }: { slug: string; titulo: string; children: ReactNode }) {
  const id = `${useContext(PrefixoDeIds)}-${slug}`;
  return (
    <section className={styles.detalheSecao} aria-labelledby={id}>
      <h4 className={styles.detalheTitulo} id={id}>{titulo}</h4>
      {children}
    </section>
  );
}

function Fato({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <>
      <dt>{rotulo}</dt>
      <dd>{children}</dd>
    </>
  );
}

const Mono = ({ children }: { children: ReactNode }) => <code>{children}</code>;

/** O nome em português da capability DO ITEM (P3 do deploy 3); outra capability, ou sem catálogo, `null`. */
type NomeDaCapability = (codigo: string) => string | null;
const nomeDaCapabilityDo = (item: EntradaDoLivro): NomeDaCapability =>
  (codigo) => (codigo === item.capability ? item.capability_nome ?? null : null);

/** "Abrir a conversa (`OPEN_THREAD`)": o nome do catálogo e o código ao lado; sem nome, só o código. */
function CapabilityNomeada({ codigo, nome }: { codigo: string; nome: string | null }) {
  return nome ? <>{nome} (<Mono>{codigo}</Mono>)</> : <Mono>{codigo}</Mono>;
}
const nomesDeParametro = (nomes: readonly string[]) => nomes.map((n, i) => (
  <span key={n}>{i > 0 ? ', ' : ''}<Mono>{`{${n}}`}</Mono></span>
));

// ---------------------------------------------------------------- identidade

function Identidade({ item, conteudo }: { item: EntradaDoLivro; conteudo: ConteudoDoItem | null }) {
  const capability = conteudo?.tipo === 'receita' ? conteudo.capability?.nomes.join(', ')
    : conteudo?.tipo === 'licao' ? conteudo.escopo.capability : null;
  const versao = conteudo?.tipo === 'receita' ? `${conteudo.identidade.versao}${conteudo.identidade.app_version ? ` · app ${conteudo.identidade.app_version}` : ''}`
    : conteudo?.tipo === 'habilidade' ? String(conteudo.versao) : null;
  return (
    <Secao slug="identidade" titulo="Identidade">
      <dl className={styles.fatos}>
        <Fato rotulo="Tipo">{rotuloDoKind(item.kind)}</Fato>
        {item.app ? (
          <Fato rotulo="Aplicativo">
            <button type="button" className={styles.linkBtn} title={`Abrir este aplicativo (${item.app})`} onClick={() => abrirApp(item.app as string)}>
              {item.app_nome && item.app_nome !== item.app ? item.app_nome : <span className={styles.mono}>{item.app}</span>}
            </button>
          </Fato>
        ) : null}
        {capability ? <Fato rotulo="Capacidade"><CapabilityNomeada codigo={capability} nome={nomeDaCapabilityDo(item)(capability)} /></Fato> : null}
        {versao ? <Fato rotulo="Versão">{versao}</Fato> : null}
        <Fato rotulo="Estado">{rotuloDoEstado(item.state)}</Fato>
        <Fato rotulo="Origem">{ORIGEM_LABEL[item.origin] ?? item.origin}</Fato>
      </dl>
    </Secao>
  );
}

// ---------------------------------------------------------------- conteúdo

function Vizinha({ rotulo, v }: { rotulo: string; v: VizinhaDaReceita | null }) {
  if (!v) return null;
  return (
    <Fato rotulo={rotulo}>
      <a className={styles.linkAlvo} href={hrefDoItem('receita', String(v.id))}>versão {v.versao} ({rotuloDoStatusDaReceita(v.estado)})</a>
    </Fato>
  );
}

function Seletor({ a }: { a: AcaoDaReceita }) {
  if (a.alvo.length === 0) return null;
  return (
    <span className={styles.passoLinha}>
      Alvo:{' '}
      {a.alvo.map((s, i) => (
        <span key={`${i}-${s.tipo}`}>
          {i > 0 ? ' · ou ' : ''}
          {(['rid', 'texto', 'desc'] as const).filter((c) => typeof s[c] === 'string').map((c, j) => (
            <span key={c}>{j > 0 ? ', ' : ''}{rotuloDoCampoDoSeletor(c)} <Mono>{s[c]}</Mono></span>
          ))}
          {typeof s.rid !== 'string' && typeof s.texto !== 'string' && typeof s.desc !== 'string' ? rotuloDoSeletor(s.tipo) : ''}
        </span>
      ))}
    </span>
  );
}

function Acao({ a }: { a: AcaoDaReceita }) {
  return (
    <li>
      <span className={styles.passoTitulo}>
        <strong>{rotuloDaFerramenta(a.ferramenta)}</strong>
        {a.commit ? <Badge tone="warning" size="sm" icon={Zap} title="É esta ação que faz o efeito externo (o commit)">faz o efeito</Badge> : null}
      </span>
      <Seletor a={a} />
      {a.segredo ? <span className={styles.passoLinha}>Digita um dado sigiloso (nome e valor nunca saem do cofre)</span> : null}
      {a.parametros.length > 0 ? <span className={styles.passoLinha}>Usa os parâmetros {nomesDeParametro(a.parametros)}</span> : null}
      {a.digita ? (
        <span className={styles.passoLinha}>
          {a.digita.limpa_antes ? 'Limpa o campo antes' : 'Não limpa o campo antes'}
          {a.digita.enter ? ' · aperta Enter no fim' : ''}
          {a.digita.so_parametro ? ' · o texto vem inteiro de parâmetros' : ''}
        </span>
      ) : null}
      {a.pacote ? <span className={styles.passoLinha}>App: <Mono>{a.pacote}</Mono></span> : null}
      {typeof a.duracao_ms === 'number' ? <span className={styles.passoLinha}>Segura por {formatInt(a.duracao_ms)} ms</span> : null}
      {a.coleta ? (
        <span className={styles.passoLinha}>
          Item da lista: {a.coleta.seletor_do_item ? <Mono>{a.coleta.seletor_do_item}</Mono> : SEM_DADO}
          {a.coleta.exclusoes > 0 ? ` · ${formatInt(a.coleta.exclusoes)} exclusões` : ''}
        </span>
      ) : null}
      {a.rolagem ? (
        <span className={styles.passoLinha}>
          Rola {a.rolagem.direcao ?? SEM_DADO}{typeof a.rolagem.max === 'number' ? `, até ${formatInt(a.rolagem.max)} vezes` : ''}
        </span>
      ) : null}
    </li>
  );
}

function OrigemDaReceitaTexto({ o }: { o: OrigemDaReceita }) {
  if (o.tipo === 'execucao') {
    return o.run_id
      ? <>Aprendida na execução <a className={styles.linkAlvo} href={hrefDaExecucao(o.run_id)}>{o.run_id}</a></>
      : <>Aprendida numa execução que não existe mais ({o.step_id})</>;
  }
  if (o.tipo === 'treino') return <>Demonstrada no treino <Mono>{o.ref}</Mono></>;
  return <span className={styles.semDado}>origem desconhecida</span>;
}

function ConteudoReceita({ c, nomeDe }: { c: ConteudoDaReceita; nomeDe: NomeDaCapability }) {
  const commits = c.efeito.acoes_commit.map((i) => i + 1);
  const unica = c.capability?.nomes.length === 1 ? c.capability.nomes[0] : undefined;
  const { uso, sombra } = c;
  return (
    <>
      <p className={styles.secaoLead}>
        {c.efeito.externo
          ? `Tem efeito fora do sistema${commits.length > 0 ? `; quem o faz ${commits.length === 1 ? 'é o passo' : 'são os passos'} ${commits.join(', ')}` : ''}.`
          : 'Não tem efeito fora do sistema.'}
      </p>
      <ol className={styles.passos} aria-label="Passos da receita">
        {c.acoes.map((a) => <Acao key={a.indice} a={a} />)}
      </ol>
      <dl className={styles.fatos}>
        {c.capability ? (
          <Fato rotulo="Capacidade">
            {unica ? <CapabilityNomeada codigo={unica} nome={nomeDe(unica)} /> : <Mono>{c.capability.nomes.join(', ')}</Mono>}
            {c.capability.ambigua ? <> <Badge tone="warning" size="sm" title="Etapas com a mesma forma servem a mais de uma capacidade: o sistema não sabe qual é a certa.">ambígua</Badge></> : null}
            {/* Travessão, não parênteses: o nome já leva o código entre parênteses. */}
            {' '}<span className={styles.passoLinha}>
              — {c.capability.fonte === 'origem' ? 'a da etapa onde foi aprendida' : 'deduzida das etapas com a mesma forma'}
            </span>
          </Fato>
        ) : null}
        <Fato rotulo="Origem"><OrigemDaReceitaTexto o={c.origem} /></Fato>
        <Fato rotulo="Uso">
          deu certo {formatInt(uso.replay_ok)} · falhou {formatInt(uso.replay_fail)} · falhas seguidas {formatInt(uso.consecutive_fail)}
          {uso.last_used_at ? ` · último uso ${formatQuando(uso.last_used_at)}` : ' · nunca usada'}
        </Fato>
        {sombra.shadow_total > 0 ? (
          <Fato rotulo="Em sombra">concordou com a IA em {formatInt(sombra.shadow_agree)} de {formatInt(sombra.shadow_total)}</Fato>
        ) : null}
        <Vizinha rotulo="Substitui" v={c.substitui} />
        <Vizinha rotulo="Substituída por" v={c.substituida_por} />
      </dl>
    </>
  );
}

function ConteudoFluxo({ c }: { c: ConteudoDoFluxo }) {
  return (
    <>
      <dl className={styles.fatos}>
        {c.nome ? <Fato rotulo="Nome">{c.nome}</Fato> : null}
        {c.comando_modelo ? <Fato rotulo="Comando modelo">{c.comando_modelo}</Fato> : null}
        <Fato rotulo="Origem">
          {c.origem.tipo === 'treino' ? 'Demonstrado no treino' : 'Aprendido de execução'}
          {c.origem.source_run_id ? <>{' '}<a className={styles.linkAlvo} href={hrefDaExecucao(c.origem.source_run_id)}>{c.origem.source_run_id}</a></> : null}
        </Fato>
        <Fato rotulo="Efeito">{c.efeito.externo ? 'tem efeito fora do sistema' : 'sem efeito fora do sistema'}</Fato>
      </dl>
      <ol className={styles.passos} aria-label="Etapas do fluxo">
        {c.etapas.map((e) => (
          <li key={e.indice}>
            <span className={styles.passoTitulo}>
              <strong>{e.chave ?? `Etapa ${e.indice + 1}`}</strong>
              {e.efeito ? <Badge tone="warning" size="sm" icon={Zap} title="Esta etapa tem efeito externo">efeito</Badge> : null}
            </span>
            {e.capability ? <span className={styles.passoLinha}>Capacidade <Mono>{e.capability}</Mono></span> : null}
            {e.alvo ? <span className={styles.passoLinha}>Alvo: <Mono>{e.alvo}</Mono></span> : null}
            {e.pos_condicao ? <span className={styles.passoLinha}>Confere: {e.pos_condicao.descricao ?? e.pos_condicao.tipo ?? SEM_DADO}</span> : null}
            {e.segredo ? <span className={styles.passoLinha}>Usa um dado sigiloso (nunca mostrado)</span> : null}
            {e.parametros.length > 0 ? <span className={styles.passoLinha}>Parâmetros {nomesDeParametro(e.parametros)}</span> : null}
          </li>
        ))}
      </ol>
    </>
  );
}

function ConteudoHabilidade({ c }: { c: ConteudoDaHabilidade }) {
  return (
    <dl className={styles.fatos}>
      <Fato rotulo="Habilidade"><Mono>{c.skill_id}</Mono> versão {c.versao}</Fato>
      {c.command_template ? <Fato rotulo="Comando">{c.command_template}</Fato> : null}
      {c.parametros.length > 0 ? <Fato rotulo="Parâmetros">{nomesDeParametro(c.parametros)}</Fato> : null}
      <Fato rotulo="Passos">{formatInt(c.total_de_nos)} nós{c.nos.length > 0 ? `: ${c.nos.map((n) => n.tipo ? `${n.id} (${n.tipo})` : n.id).join(', ')}` : ''}</Fato>
      {c.source_kind ? <Fato rotulo="Veio de">{c.source_kind}{c.source_ref ? ` · ${c.source_ref}` : ''}</Fato> : null}
      {c.parent_version !== null ? <Fato rotulo="Versão anterior">{c.parent_version}</Fato> : null}
    </dl>
  );
}

function ConteudoLicao({ c, nomeDe }: { c: ConteudoDaLicao; nomeDe: NomeDaCapability }) {
  const esc = c.escopo;
  const nome = esc.capability ? nomeDe(esc.capability) : null;
  return (
    <>
      {/* Na tela, "Em Abrir o perfil (OPEN_PROFILE):"; o texto gravado, que vai ao prompt, segue com o código. */}
      {c.texto ? <blockquote className={styles.citacao}>{nomearCapabilityNoTexto(c.texto, esc.capability, nome)}</blockquote>
        : <p className={styles.semDado}>texto indisponível</p>}
      <dl className={styles.fatos}>
        {c.acao ? <Fato rotulo="Orienta a ação">{c.acao}</Fato> : null}
        {c.alvo?.valor ? <Fato rotulo="Sobre">{rotuloDoAlvoDaLicao(c.alvo.tipo)} <Mono>{c.alvo.valor}</Mono></Fato> : null}
        {esc.capability ? <Fato rotulo="Capacidade"><CapabilityNomeada codigo={esc.capability} nome={nome} /></Fato> : null}
        {esc.role ? <Fato rotulo="Papel">{esc.role}</Fato> : null}
        {c.modelo ? <Fato rotulo="Modelo">{c.modelo}</Fato> : null}
        {typeof c.tokens === 'number' ? <Fato rotulo="Tamanho">{formatInt(c.tokens)} tokens no prompt</Fato> : null}
      </dl>
    </>
  );
}

function ConteudoTela({ c }: { c: ConteudoDaTela }) {
  return (
    <>
      <dl className={styles.fatos}>
        {c.tela ? <Fato rotulo="Tela"><Mono>{c.tela}</Mono></Fato> : null}
        <Fato rotulo="Reconhecida">{c.casa ? 'a regra declarada casa com ela' : 'a regra declarada não casa'}</Fato>
        <Fato rotulo="Exige login">{c.autenticada ? 'sim' : 'não'}</Fato>
        {c.razao ? <Fato rotulo="Razão">{c.razao}</Fato> : null}
      </dl>
      {c.ids_todos.length > 0 ? (
        <p className={styles.passoLinha}>Ids que a identificam: {c.ids_todos.map((id, i) => <span key={id}>{i > 0 ? ', ' : ''}<Mono>{id}</Mono></span>)}</p>
      ) : null}
    </>
  );
}

function Conteudo({ c, nomeDe }: { c: ConteudoDoItem; nomeDe: NomeDaCapability }) {
  switch (c.tipo) {
    case 'receita': return <ConteudoReceita c={c} nomeDe={nomeDe} />;
    case 'fluxo': return <ConteudoFluxo c={c} />;
    case 'habilidade': return <ConteudoHabilidade c={c} />;
    case 'licao': return <ConteudoLicao c={c} nomeDe={nomeDe} />;
    case 'tela': return <ConteudoTela c={c} />;
    default: return null;
  }
}

// ---------------------------------------------------------------- saúde

/** `comVersao`: a seção "Versão do app" vem logo abaixo; a linha "Versão do app: sem dado" da saúde a contradiria. */
function Saude({ s, comVersao = false }: { s: SaudeDoItem; comVersao?: boolean }) {
  const dimensoes = comVersao ? s.dimensoes.filter((d) => !(d.nome === 'versao' && (d.estado === 'desconhecida' || d.valor === null))) : s.dimensoes;
  const meta = metaDeSaude(s.rotulo);
  return (
    <Secao slug="saude" titulo="Saúde">
      {meta ? <p><Badge tone={meta.tone} icon={meta.icon} title={meta.description}>{meta.label}</Badge> <span className={styles.secaoLead}>{meta.description}</span></p> : null}
      {s.motivos.length > 0 ? (
        <ul className={styles.motivos} aria-label="Por que este rótulo">
          {s.motivos.map((m, i) => <li key={`${m.codigo}-${i}`}>{textoDoMotivo(m)}</li>)}
        </ul>
      ) : null}
      {dimensoes.length > 0 ? (
        <table className={styles.tabelaDetalhe}>
          <caption>O que foi medido</caption>
          <thead><tr><th scope="col">Medida</th><th scope="col">Valor</th></tr></thead>
          <tbody>
            {dimensoes.map((d) => (
              <tr key={d.nome}>
                <th scope="row">{rotuloDaDimensao(d.nome)}</th>
                <td title={`Fonte: ${d.fonte}`}>
                  {d.estado === 'desconhecida' || d.valor === null ? <span className={styles.semDado}>{SEM_DADO}</span> : valorDaDimensao(d)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Secao>
  );
}

// ---------------------------------------------------------------- versão

function Versao({ v, appNome }: { v: VersaoDoItem; appNome: string | null }) {
  const meta = metaDeVersao(v.estado);
  return (
    <Secao slug="versao" titulo="Versão do app">
      <p>
        <Badge tone={meta.tone} title={meta.description}>{meta.label}</Badge>{' '}
        <span className={styles.secaoLead}>{meta.description}</span>
      </p>
      <dl className={styles.fatos}>
        {v.app ? (
          <Fato rotulo="Aplicativo">
            {appNome && appNome !== v.app ? <span title={v.app}>{appNome}</span> : <Mono>{v.app}</Mono>}
            {v.app_version ? ` · versão ${v.app_version}` : ''}
          </Fato>
        ) : null}
        <Fato rotulo="Vivas no parque">
          {v.vivas.length > 0 ? v.vivas.map((x) => `${x.versao} (${textoDeAparelhos(x.aparelhos)})`).join(' · ')
            : <span className={styles.semDado}>nenhuma versão observada</span>}
        </Fato>
        {v.nao_testada_em.length > 0 ? <Fato rotulo="Não testada em">{v.nao_testada_em.join(', ')}</Fato> : null}
      </dl>
      {v.por_versao.length > 0 ? (
        <table className={styles.tabelaDetalhe}>
          <caption>Quadro por versão</caption>
          <thead><tr><th scope="col">Versão</th><th scope="col">No parque</th><th scope="col">Situação</th><th scope="col">Receita</th></tr></thead>
          <tbody>
            {v.por_versao.map((p) => (
              <tr key={p.versao}>
                <th scope="row">{p.versao}</th>
                <td>{p.viva ? textoDeAparelhos(p.aparelhos) : 'não'}</td>
                <td>{metaDeVersao(p.estado).label}</td>
                <td>{p.receita_ref ? <a className={styles.linkAlvo} href={hrefDoItem('receita', p.receita_ref)}>receita {p.receita_ref}</a> : <span className={styles.semDado}>nenhuma</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Secao>
  );
}

// ---------------------------------------------------------------- evidência, histórico, relações, ações

const POSTURA: Record<EvidenciaDoLivro['stance'], string> = { for: 'a favor', against: 'contra', conflict: 'em conflito' };

function Evidencia({ evid }: { evid: readonly EvidenciaDoLivro[] }) {
  const n = (s: EvidenciaDoLivro['stance']) => evid.filter((x) => x.stance === s).length;
  return (
    <Secao slug="evidencia" titulo="Evidência registrada">
      <p className={styles.secaoLead}>
        As evidências que o Livro guardou (execução, aparelho e versão de cada uma); os usos da linha do item contam as
        reproduções.{' '}
        {n('for')} a favor · {n('against')} contra · {n('conflict')} em conflito
        {evid.some((x) => x.simulated) ? ' (as simuladas nunca contam para publicar)' : ''}.
      </p>
      {evid.length > 0 ? (
        <ul className={styles.motivos} aria-label="Evidências">
          {evid.map((x, i) => (
            <li key={`${x.origin_ref}-${i}`}>
              {POSTURA[x.stance] ?? x.stance}
              {x.run_id ? <>{' · execução '}<a className={styles.linkAlvo} href={hrefDaExecucao(x.run_id)}>{x.run_id}</a></> : ''}
              {x.instance_id ? ` · aparelho ${x.instance_id}` : ''}
              {x.app_version ? ` · app ${x.app_version}` : ''}
              {` · ${formatDateTime(x.observed_at)}`}
              {x.simulated ? ' · simulada' : ''}
              {x.detail ? ` · ${x.detail}` : ''}
              {x.invalidada ? (
                <>{' · '}<Badge tone="danger" size="sm">execução invalidada</Badge> não conta como prova</>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </Secao>
  );
}

/** O passo da trilha em palavras. `disabled → disabled` é a marca que muda só o tipo do desligamento (30.23);
 *  `published → published` com `tipo: confirmacao` é "Confirmar que fica" (30.24). */
export function passoDaTransicao(t: Pick<TransicaoDoLivro, 'from' | 'to' | 'tipo'>): string {
  if (t.tipo === 'confirmacao') return 'Confirmado que fica';
  if (t.from && t.from === t.to) return `${rotuloDoEstado(t.to)} (motivo reclassificado)`;
  return `${t.from ? `${rotuloDoEstado(t.from)} → ` : ''}${rotuloDoEstado(t.to)}`;
}

function Historico({ trilha }: { trilha: DetalheDoLivro['trilha'] }) {
  return (
    <Secao slug="historico" titulo="Histórico">
      {trilha.length > 0 ? (
        <ol className={styles.trilha} aria-label="Trilha">
          {trilha.map((t) => (
            <li key={t.id} data-tipo={t.tipo ?? undefined}>
              {formatQuando(t.decided_at)} · {passoDaTransicao(t)} por <strong>{t.decided_by}</strong>
              {t.tipo === 'confirmacao' ? (t.motivo_da_pessoa ? `: ${t.motivo_da_pessoa}` : ' (sem motivo)') : ': '}
              {t.tipo === 'confirmacao' ? null : t.tipo === 'evidencia_invalida' && t.run_invalidada ? (
                <>
                  <Badge tone="danger" size="sm">evidência inválida</Badge>{' '}
                  a execução <a className={styles.linkAlvo} href={hrefDaExecucao(t.run_invalidada)}>{t.run_invalidada}</a>{' '}
                  terminou como sucesso sem comprovar o que fez
                </>
              ) : t.reason}
            </li>
          ))}
        </ol>
      ) : <p className={styles.secaoLead}>Nenhuma transição registrada pelo livro ainda.</p>}
    </Secao>
  );
}

function Relacoes({ relacoes }: { relacoes: readonly RelacaoDoItem[] }) {
  return (
    <Secao slug="relacoes" titulo="Relações">
      <ul className={styles.motivos} aria-label="Itens relacionados">
        {relacoes.map((r, i) => {
          const destino = destinoDaRelacao(r);
          return (
            <li key={`${r.tipo}-${r.kind}-${r.ref}-${i}`}>
              {rotuloDaRelacao(r.tipo)}{' '}
              {destino ? (
                <a className={styles.linkAlvo} href={hrefDoItem(destino.kind, destino.ref)}>
                  {rotuloDoKind(destino.kind)} {r.rotulo ?? destino.ref}
                </a>
              ) : textoDoAlvoSemItem(r)}
            </li>
          );
        })}
      </ul>
    </Secao>
  );
}

/** 30.23: por que o item espera o dono depois de uma evidência inválida, com o caminho até o item desligado. */
function Reaprendimento({ item }: { item: EntradaDoLivro }) {
  const r = item.reaprendido;
  if (!r) return null;
  const propria = r.item.kind === item.kind && r.item.ref === item.ref;
  const destino = propria ? null : destinoDaRelacao(r.item);
  const execucao = <a className={styles.linkAlvo} href={hrefDaExecucao(r.run_invalidada)}>{r.run_invalidada}</a>;
  return (
    <Secao slug="reaprendido" titulo="Reaprendido depois de uma evidência inválida">
      <p className={styles.avisoDoItem} data-reaprendido>
        {propria ? (
          <>Esta linha tinha sido aprendida da execução {execucao}, que terminou como sucesso sem comprovar o que fez, e
            foi desligada por isso. Outra execução real a ensinou de novo, na mesma linha.</>
        ) : (
          <>Reaprende o item{' '}
            {destino ? (
              <a className={styles.linkAlvo} href={hrefDoItem(destino.kind, destino.ref)}>
                {rotuloDoKind(destino.kind)} {destino.ref}
              </a>
            ) : `${r.item.kind} ${r.item.ref}`}
            , aprendido da execução {execucao}, que terminou como sucesso sem comprovar o que fez. Outra execução real
            ensinou o mesmo de novo.</>
        )}{' '}
        O sistema não publica sozinho o que é reaprendido assim: a aprovação é sua.
      </p>
    </Secao>
  );
}

/**
 * 30.23: marcar a execução de origem como evidência inválida. Sem motivo livre (o backend grava o tipo estruturado) e
 * sem modal: a confirmação abre no lugar do botão e diz o que acontece com o item.
 */
function MarcarEvidenciaInvalida({ item, runId, onFeito }: { item: EntradaDoLivro; runId: string; onFeito?: () => void }) {
  const [aberta, setAberta] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const confirmar = async () => {
    setEnviando(true);
    setErro(null);
    try {
      await apiAprendizado.invalidarEvidencia(item.kind, item.ref, runId);
      setAberta(false);
      onFeito?.();
    } catch (err) {
      const recusa = toApiError(err);
      setErro(`${recusa.message} ${hintForError(recusa)}`.trim());
    } finally {
      setEnviando(false);
    }
  };
  if (!aberta) {
    return (
      <div className={styles.itemAcoes}>
        <Button size="sm" variant="dangerGhost" onClick={() => setAberta(true)}>Marcar evidência inválida</Button>
      </div>
    );
  }
  return (
    <div className={styles.confirmacao} data-evidencia-invalida>
      <p className={styles.secaoLead}>
        A execução <a className={styles.linkAlvo} href={hrefDaExecucao(runId)}>{runId}</a>, de onde este item foi
        aprendido, terminou como sucesso sem comprovar o que fez?{' '}
        {item.state === 'disabled'
          ? 'O item já está desligado: a marca só registra este motivo na trilha.'
          : 'O item é desligado agora.'}{' '}
        Ele não volta por essa execução. Se outra execução real ensinar o mesmo, ele renasce como candidato e espera a
        sua aprovação.
      </p>
      {erro ? <p className={styles.erroInline} role="alert">{erro}</p> : null}
      <div className={styles.decisaoAcoes}>
        <Button size="sm" variant="danger" loading={enviando} onClick={() => void confirmar()}>
          Confirmar evidência inválida
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setAberta(false)} disabled={enviando}>Cancelar</Button>
      </div>
    </div>
  );
}

function Acoes({ item, invalidar, onMudou }: { item: EntradaDoLivro; invalidar: string | null; onMudou?: () => void }) {
  const acoes = acoesDoItem(item);
  const porQue = porQueOSistemaNaoPublica(item);
  if (acoes.length === 0 && !porQue && !invalidar) return null;
  return (
    <Secao slug="acoes" titulo="O que você pode fazer">
      {acoes.length > 0 ? (
        <p className={styles.secaoLead}>
          {acoes.map((a) => `${a.label} (vai para ${rotuloDoEstado(a.to).toLowerCase()})`).join(' · ')}. Use os botões da linha do item; o motivo fica na trilha.
        </p>
      ) : null}
      {porQue ? <p className={styles.secaoLead}>O sistema não publica sozinho: {porQue}.</p> : null}
      {invalidar ? <MarcarEvidenciaInvalida item={item} runId={invalidar} onFeito={onMudou} /> : null}
    </Secao>
  );
}

// ---------------------------------------------------------------- o detalhe

/**
 * As seções do §11.2 do desenho, só as aplicáveis: o que o backend não mandou (campo ausente ou `null`) não ganha
 * seção, e o que ele mandou como "sem dado" aparece assim, nunca como zero. `onMudou`: um gesto no detalhe (marcar a
 * evidência inválida, aceitar ou recusar o parecer da IA, pedir revisão) mudou o item; quem mostra o detalhe o relê.
 */
export function DetalheRico({ detalhe, onMudou }: { detalhe: DetalheDoLivro; onMudou?: () => void }) {
  const item = detalhe.item;
  const conteudo = detalhe.conteudo ?? null;
  const saude = item.saude ?? null;
  const versao = detalhe.versao && detalhe.versao.estado !== 'independente' ? detalhe.versao : null;
  const evid = Array.isArray(detalhe.evidencias) ? detalhe.evidencias : [];
  const trilha = Array.isArray(detalhe.trilha) ? detalhe.trilha : [];
  const relacoes = Array.isArray(detalhe.relacoes) ? detalhe.relacoes : [];
  const prefixo = useId();
  return (
    <PrefixoDeIds.Provider value={prefixo}>
      <div className={styles.detalhe}>
        <Identidade item={item} conteudo={conteudo} />
        <Reaprendimento item={item} />
        {conteudo ? (
          <Secao slug="conteudo" titulo="Conteúdo"><Conteudo c={conteudo} nomeDe={nomeDaCapabilityDo(item)} /></Secao>
        ) : null}
        {saude ? <Saude s={saude} comVersao={!!versao} /> : null}
        {versao ? <Versao v={versao} appNome={versao.app === item.app ? item.app_nome ?? null : null} /> : null}
        <Evidencia evid={evid} />
        <Historico trilha={trilha} />
        {relacoes.length > 0 ? <Relacoes relacoes={relacoes} /> : null}
        <SecaoDoParecer item={item} pareceres={Array.isArray(detalhe.pareceres) ? detalhe.pareceres : []}
                        curador={detalhe.curador} onMudou={onMudou} />
        <Acoes item={item} invalidar={detalhe.invalidar_evidencia?.run_id ?? null} onMudou={onMudou} />
      </div>
    </PrefixoDeIds.Provider>
  );
}
