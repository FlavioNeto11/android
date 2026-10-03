import { Bot, RefreshCw } from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { hintForError, toApiError } from '../../api/client';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { hashDe } from '../../lib/rotas';
import { formatDateTime, formatQuando } from '../../lib/time';
import { apiAprendizado } from './api';
import { DecisaoInline } from './DecisaoInline';
import type { EntradaDoLivro } from './model';
import {
  type BlocoDoCurador, type ParecerDaIA, type ParecerNaFila, ladoDaDecisao, rotuloDoAceite, seloDaClasse,
  textoDaCausa, textoDaConfianca, textoDaDecisao, textoDaDecisaoFinal, textoDaFalta, textoDaInconsistencia,
  textoDoResultadoPosterior,
  textoDaRecusa, textoDaValidade, textoDoGatilho, textoDoRisco,
} from './parecer';
import styles from './Aprendizado.module.css';

const TOM_DO_LADO = { sobe: 'success', desce: 'warning', espera: 'neutral' } as const;
const TOM_DA_CLASSE = { A: 'muted', B: 'info', C: 'danger' } as const;

/** Um id citado pela IA, com link quando aponta algo que o painel abre (execução, outro item do Livro). */
function Citado({ id }: { id: string }) {
  const [prefixo, resto] = [id.slice(0, id.indexOf(':')), id.slice(id.indexOf(':') + 1)];
  if (prefixo === 'run' && resto) {
    return <a className={styles.linkAlvo} href={hashDe('execucoes', { segmentos: [resto] })}><code>{id}</code></a>;
  }
  if (['receita', 'fluxo', 'licao', 'tela', 'voz', 'preferencia'].includes(prefixo) && resto) {
    return <a className={styles.linkAlvo} href={hashDe('aprendizado', { query: { aba: 'aprendido', item: id } })}><code>{id}</code></a>;
  }
  return <code>{id}</code>;
}

function Fato({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <>
      <dt>{rotulo}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** O selo compacto do parecer na linha da fila: o que a IA sugere, com que confiança e em que classe. */
export function ParecerNaLinha({ p }: { p: ParecerNaFila }) {
  const classe = seloDaClasse(p.classe, p.recusa, p.classe_no_parecer);
  return (
    <p className={styles.parecerNaLinha} data-parecer-na-linha={p.id}>
      <Bot size={14} aria-hidden />
      <span>Parecer do curador: <strong>{textoDaDecisao(p.decisao).toLowerCase()}</strong> · {textoDaConfianca(p.confianca)}</span>
      {classe && p.classe ? <Badge tone={p.recusa ? 'muted' : TOM_DA_CLASSE[p.classe]} size="sm" title={classe.explica}>{classe.selo}</Badge> : null}
      {classe?.registro ? <Badge tone="muted" size="sm" title={textoDaRecusa(p.recusa) ?? undefined}>{classe.registro}</Badge> : null}
      <span className={styles.parecerDica}>O parecer completo fica em “Detalhes”.</span>
    </p>
  );
}

function CartaoDoParecer({ p, item, onMudou }: { p: ParecerDaIA; item: EntradaDoLivro; onMudou?: () => void }) {
  const [gesto, setGesto] = useState<'aceitar' | 'recusar' | null>(null);
  const s = p.parecer;
  if (!s) return null;
  const classe = seloDaClasse(p.classe, p.recusa, p.classe_no_parecer);
  const aceite = rotuloDoAceite(p.acao);
  const responder = async (resposta: 'aceitar' | 'recusar', motivo: string): Promise<string | null> => {
    try {
      await apiAprendizado.responderParecer(item.kind, item.ref, p.id, { resposta, motivo });
      setGesto(null);
      onMudou?.();
      return null;
    } catch (err) {
      const e = toApiError(err);
      return textoDaRecusa(e.code) === e.code ? `${e.message} ${hintForError(e)}`.trim() : textoDaRecusa(e.code);
    }
  };
  return (
    <div className={styles.parecer} data-parecer={p.id}>
      <div className={styles.parecerCabeca}>
        <span className={styles.parecerSugestao}>O curador sugere: <strong>{textoDaDecisao(s.decisao)}</strong></span>
        <Badge tone={TOM_DO_LADO[ladoDaDecisao(s.decisao)]} size="sm">{textoDaConfianca(s.confianca)}</Badge>
        {classe && p.classe ? <Badge tone={p.recusa ? 'muted' : TOM_DA_CLASSE[p.classe]} size="sm" title={classe.explica}>{classe.selo}</Badge> : null}
        {p.simulated ? <Badge tone="muted" size="sm" title="Provedor de teste: não é um parecer real do curador">simulado</Badge> : null}
      </div>
      {s.conclusao ? <p className={styles.citacao}>{s.conclusao}</p> : null}
      <dl className={styles.fatos}>
        {s.causa ? <Fato rotulo="Por quê">{textoDaCausa(s.causa)}</Fato> : null}
        {s.riscos.length > 0 ? <Fato rotulo="Riscos">{s.riscos.map(textoDoRisco).join(', ')}</Fato> : null}
        {s.inconsistencias.length > 0 ? <Fato rotulo="Inconsistências">{s.inconsistencias.map(textoDaInconsistencia).join(', ')}</Fato> : null}
        {s.falta.length > 0 ? <Fato rotulo="Faltaria">{s.falta.map(textoDaFalta).join(', ')}</Fato> : null}
        {s.alvo ? <Fato rotulo="Outro item"><Citado id={s.alvo} /></Fato> : null}
        {s.evidencias_citadas.length > 0 ? (
          <Fato rotulo="Citou">
            {s.evidencias_citadas.map((c, i) => <span key={c}>{i > 0 ? ', ' : ''}<Citado id={c} /></span>)}
          </Fato>
        ) : null}
        <Fato rotulo="Revisado">
          <span title={formatDateTime(p.criado_em)}>{formatQuando(p.criado_em)}</span>
          {` · motivo: ${textoDoGatilho(p.gatilho)}`}{p.modelo ? ` · ${p.modelo}` : ''}
        </Fato>
        {textoDoResultadoPosterior(p.resultado_posterior) ? (
          <Fato rotulo="Em 14 dias">{textoDoResultadoPosterior(p.resultado_posterior)}</Fato>
        ) : null}
      </dl>
      {p.recusa ? <p className={styles.notaDoItem}>{textoDaRecusa(p.recusa)}</p>
        : gesto ? (
          <DecisaoInline
            acao={gesto === 'aceitar' ? aceite : { confirmar: 'Confirmar recusa', perigo: false }}
            rotulo={gesto === 'aceitar' ? 'Motivo do aceite' : 'Por que você recusa o parecer'}
            dica={gesto === 'aceitar'
              ? (p.acao ? 'Fica na trilha do item e no registro do parecer, com o seu nome.' : 'Fica no registro do parecer, com o seu nome. O item não muda.')
              : 'Fica no registro do parecer, com o seu nome. O item não muda: decida-o pelos botões dele.'}
            semMotivo={gesto === 'aceitar' && p.acao ? undefined : 'Diga o motivo: fica no registro do parecer.'}
            onCancelar={() => setGesto(null)}
            onConfirmar={(motivo) => responder(gesto, motivo)}
          />
        ) : (
          <div className={styles.itemAcoes}>
            <Button size="sm" variant={aceite.perigo ? 'danger' : 'primary'} onClick={() => setGesto('aceitar')}>{aceite.label}</Button>
            <Button size="sm" variant="secondary" onClick={() => setGesto('recusar')}>Recusar o parecer</Button>
          </div>
        )}
    </div>
  );
}

function PedirRevisao({ item, onMudou }: { item: EntradaDoLivro; onMudou?: () => void }) {
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<{ erro: boolean; texto: string } | null>(null);
  const pedir = async () => {
    setEnviando(true);
    setAviso(null);
    try {
      const r = await apiAprendizado.pedirRevisao(item.kind, item.ref);
      if (r.pedido) {
        setAviso({ erro: false, texto: 'Pedido registrado: o curador revisa este item na próxima volta, dentro do orçamento.' });
      } else {
        const quando = r.revisao ? ` (${formatQuando(r.revisao.criado_em)})` : '';
        setAviso({ erro: false, texto: `O item já foi revisado como está agora${quando}. Um pedido novo vale quando ele mudar (evidência nova, outro estado).` });
        onMudou?.();
      }
    } catch (err) {
      const e = toApiError(err);
      setAviso({ erro: true, texto: textoDaRecusa(e.code) === e.code ? e.message : textoDaRecusa(e.code) ?? e.message });
    }
    setEnviando(false);
  };
  return (
    <div className={styles.pedirRevisao}>
      <Button size="sm" variant="ghost" icon={RefreshCw} loading={enviando} onClick={() => void pedir()}>Pedir revisão ao curador</Button>
      {aviso ? <p role="status" className={aviso.erro ? styles.erroInline : styles.secaoLead}>{aviso.texto}</p> : null}
    </div>
  );
}

function Anterior({ p }: { p: ParecerDaIA }) {
  const decidiu = textoDaDecisaoFinal(p);
  const sugestao = p.parecer ? `sugeriu ${textoDaDecisao(p.parecer.decisao).toLowerCase()}` : textoDaValidade(p.validade);
  return (
    <li>
      <span title={formatDateTime(p.criado_em)}>{formatQuando(p.criado_em)}</span> · {sugestao}
      {p.parecer && p.simulated ? ' (simulado)' : ''}
      {decidiu ? ` — ${decidiu}` : p.parecer ? ' — sem decisão' : ''}
      {textoDoResultadoPosterior(p.resultado_posterior) ? ` · em 14 dias, ${textoDoResultadoPosterior(p.resultado_posterior)}` : ''}
    </li>
  );
}

/**
 * A seção "Parecer da IA" do detalhe (§11.2, 30.17). Em `on`: o parecer pendente com o passo do aceite e a recusa com
 * motivo, e o pedido de revisão. Em `shadow`: só o aviso de que há parecer (ele aparece depois da decisão, para medir a
 * IA sem influenciar a pessoa) e os já decididos. Sem curador composto, nada.
 */
export function SecaoDoParecer({ item, pareceres, curador, onMudou }: {
  item: EntradaDoLivro;
  pareceres: readonly ParecerDaIA[];
  curador: BlocoDoCurador | null | undefined;
  onMudou?: () => void;
}) {
  const id = useId();
  if (!curador) return null;
  const pendente = pareceres.find((p) => p.atual) ?? null;
  const anteriores = pareceres.filter((p) => p !== pendente);
  const oculto = curador.modo !== 'on' && curador.pendentes_ocultos > 0;
  if (!pendente && anteriores.length === 0 && !oculto && !curador.pode_pedir_revisao) return null;
  return (
    <section className={styles.detalheSecao} aria-labelledby={id} data-secao-parecer>
      <h4 className={styles.detalheTitulo} id={id}>Parecer do curador</h4>
      {oculto ? (
        <p className={styles.notaDoItem}>
          Há um parecer do curador sobre este item. Ele aparece depois da sua decisão: com o curador em sombra, a sua
          escolha mede se ele acerta, sem a influência dele.
        </p>
      ) : null}
      {pendente ? <CartaoDoParecer p={pendente} item={item} onMudou={onMudou} /> : null}
      {!pendente && curador.modo === 'on' && anteriores.length === 0 ? (
        <p className={styles.secaoLead}>O curador ainda não revisou este item.</p>
      ) : null}
      {curador.pode_pedir_revisao ? <PedirRevisao item={item} onMudou={onMudou} /> : null}
      {anteriores.length > 0 ? (
        <>
          <h5 className={styles.detalheSub}>{pendente ? 'Pareceres anteriores' : 'Pareceres'}</h5>
          <ul className={styles.motivos} aria-label="Pareceres do curador">
            {anteriores.map((p) => <Anterior key={p.id} p={p} />)}
          </ul>
        </>
      ) : null}
    </section>
  );
}
