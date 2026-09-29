import { Zap } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { Checkbox, Field, TextInput } from '../../components/Field';
import { StatusBadge } from '../../components/StatusBadge';
import { cx, formatInt } from '../../lib/format';
import { saveJson } from '../../lib/storage';
import { formatClock } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { apiAprendizado } from './api';
import {
  type AcaoDoItem, type DetalheDoLivro, type EntradaDoLivro, ESTADO_META, MOTIVO_MAX, ONDE_FICAM_AS_HABILIDADES,
  ORIGEM_LABEL, erroDoMotivo, porQueOSistemaNaoPublica, refDaHabilidade, rotuloDoDetalhe, rotuloDoEstado, rotuloDoKind,
} from './model';
import styles from './Aprendizado.module.css';

export const chaveDoItem = (e: Pick<EntradaDoLivro, 'kind' | 'ref'>) => `${e.kind}:${e.ref}`;

/**
 * O motivo que toda decisão sobre o livro exige (fica na trilha, `learning_transitions.reason`). Em linha, nunca
 * modal: quem decide vê o item ao lado do que está escrevendo.
 */
export function DecisaoInline({ acao, rotulo = 'Motivo', onConfirmar, onCancelar }: {
  acao: Pick<AcaoDoItem, 'confirmar' | 'perigo'>;
  rotulo?: string;
  /** Devolve a mensagem de erro, ou `null` quando deu certo. */
  onConfirmar: (motivo: string) => Promise<string | null>;
  onCancelar: () => void;
}) {
  const [motivo, setMotivo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const invalido = erroDoMotivo(motivo);

  const enviar = async () => {
    if (invalido || enviando) return;
    setEnviando(true);
    setErro(null);
    const falha = await onConfirmar(motivo.trim());
    setEnviando(false);
    if (falha) setErro(falha);
  };

  return (
    <form className={styles.decisao} onSubmit={(e) => { e.preventDefault(); void enviar(); }}>
      <Field label={rotulo} hint="Fica na trilha do item, com o seu nome." error={erro} className={styles.decisaoCampo}>
        {({ id, describedBy, invalid }) => (
          <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={motivo} maxLength={MOTIVO_MAX}
                     autoFocus placeholder="Ex.: conferi a evidência e o alvo está certo"
                     onChange={(e) => setMotivo(e.target.value)} />
        )}
      </Field>
      <div className={styles.decisaoAcoes}>
        <Button type="submit" size="sm" variant={acao.perigo ? 'danger' : 'primary'} loading={enviando} disabledReason={invalido}>
          {acao.confirmar}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancelar} disabled={enviando}>Cancelar</Button>
      </div>
    </form>
  );
}

/**
 * Aplica UMA transição e devolve o erro legível (ou `null`). O motivo nunca sai daqui para um toast ou log.
 *
 * Habilidade vai direto pela rota das habilidades (`POST /api/skills/{id}/versions/{n}/status`): a do livro a recusa
 * com 409 `use_skills_route`, e mandar lá primeiro só gastaria uma ida. Quem decide continua sendo a pessoa da sessão
 * (a rota registra o operador); a recusa do domínio dela (comando duplicado, transição proibida) volta como a de
 * qualquer item.
 */
export async function aplicarTransicao(e: EntradaDoLivro, acao: Pick<AcaoDoItem, 'to'>, motivo: string): Promise<string | null> {
  try {
    if (e.kind === 'habilidade') {
      const ref = refDaHabilidade(e.ref);
      if (!ref) return `A referência "${e.ref}" não diz a versão: decida em ${ONDE_FICAM_AS_HABILIDADES}.`;
      await api.transitionSkill(ref.skillId, ref.version, { to: acao.to, reason: motivo });
    } else {
      await apiAprendizado.mudarEstado(e.kind, e.ref, acao.to, motivo);
    }
    return null;
  } catch (err) {
    const recusa = toApiError(err);
    return `${recusa.message} ${hintForError(recusa)}`.trim();
  }
}

/** Abre Configuração já em Fluxos e receitas, com a seção Habilidades aberta (as chaves que a página lembra, como faz
 *  `infra/CriarAparelho.tsx` para Limites). */
function abrirHabilidades(): void {
  saveJson('settingsSection', 'fluxos');
  saveJson('settings.section.habilidades', true);
  useUiStore.getState().setView('configuracao');
}

/**
 * O aviso da habilidade: ciclo próprio, publicar é sempre de uma pessoa. Na fila, diz o que publicar faz (a mesma regra
 * do diálogo de Configuração); no catálogo, onde ficam as outras decisões. Nos dois, o caminho até o ciclo completo.
 */
export function AvisoDaHabilidade({ naFila }: { naFila: boolean }) {
  return (
    <p className={styles.secaoLead}>
      {naFila
        ? 'Habilidade tem ciclo próprio e publicar é sempre de uma pessoa: publicar aqui usa a rota das habilidades. '
          + 'A versão publicada desta habilidade, se houver, é substituída na mesma operação; a publicação é recusada se '
          + 'um fluxo ativo ou outra habilidade publicada tiver o mesmo comando. '
        : 'Habilidade tem ciclo próprio e publicar é sempre de uma pessoa: publicar, recolher e desabilitar ficam em '}
      <button type="button" className={styles.linkBtn} onClick={abrirHabilidades}>
        {naFila ? `Ciclo completo em ${ONDE_FICAM_AS_HABILIDADES}` : ONDE_FICAM_AS_HABILIDADES}
      </button>
      {naFila ? '' : '.'}
    </p>
  );
}

function DetalheDoItem({ entrada }: { entrada: EntradaDoLivro }) {
  const [detalhe, setDetalhe] = useState<DetalheDoLivro | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    apiAprendizado.detalhe(entrada.kind, entrada.ref, ctl.signal)
      .then((d) => setDetalhe(d))
      .catch((e: unknown) => {
        if (!ctl.signal.aborted) setErro(toApiError(e).message);
      });
    return () => ctl.abort();
  }, [entrada.kind, entrada.ref]);
  if (erro) return <p className={styles.erroInline}>{erro}</p>;
  if (!detalhe) return <p className={styles.secaoLead}>Carregando a evidência e a trilha…</p>;
  const evid = Array.isArray(detalhe.evidencias) ? detalhe.evidencias : [];
  const trilha = Array.isArray(detalhe.trilha) ? detalhe.trilha : [];
  return (
    <div className={styles.secao}>
      <p className={styles.secaoLead}>
        Evidência: {evid.filter((x) => x.stance === 'for').length} a favor · {evid.filter((x) => x.stance === 'against').length} contra
        · {evid.filter((x) => x.stance === 'conflict').length} em conflito
        {evid.some((x) => x.simulated) ? ' (as simuladas nunca contam para publicar)' : ''}.
      </p>
      {trilha.length > 0 ? (
        <ol className={styles.trilha} aria-label="Trilha">
          {trilha.map((t) => (
            <li key={t.id}>
              {formatClock(t.decided_at)} · {t.from ? `${rotuloDoEstado(t.from)} → ` : ''}{rotuloDoEstado(t.to)} por{' '}
              <strong>{t.decided_by}</strong>: {t.reason}
            </li>
          ))}
        </ol>
      ) : <p className={styles.secaoLead}>Nenhuma transição registrada pelo livro ainda.</p>}
    </div>
  );
}

interface ItemDoLivroProps {
  entrada: EntradaDoLivro;
  /** As ações desta lista (a fila oferece aprovar e rejeitar; "Revisar", rebaixar; o catálogo, todas as da pessoa). */
  acoes: readonly AcaoDoItem[];
  selecionado?: boolean;
  onSelecionar?: (sim: boolean) => void;
  onMudou: () => void;
  extra?: ReactNode;
}

/** Uma linha do livro: o que é, em que estado, por que espera o dono e o que a pessoa pode fazer. */
export function ItemDoLivro({ entrada: e, acoes, selecionado, onSelecionar, onMudou, extra }: ItemDoLivroProps) {
  const [aberta, setAberta] = useState<AcaoDoItem | null>(null);
  const espera = e.requires_owner ? porQueOSistemaNaoPublica(e) : null;
  const detalhe = rotuloDoDetalhe(e.detail);
  const memoria = e.kind === 'memoria';

  return (
    <li className={cx(styles.item, selecionado && styles.itemSelecionado)} data-item={chaveDoItem(e)}>
      <div className={styles.itemHead}>
        {onSelecionar ? (
          <Checkbox aria-label={`Selecionar ${e.title}`} checked={!!selecionado} onChange={(ev) => onSelecionar(ev.target.checked)} />
        ) : null}
        <Badge tone="neutral" size="sm">{rotuloDoKind(e.kind)}</Badge>
        <span className={styles.itemTitulo}>{e.title || e.ref}</span>
        {e.side_effect ? <Badge tone="warning" size="sm" icon={Zap} title="Tem efeito externo (mensagem, publicação, envio…)">efeito externo</Badge> : null}
        {e.state ? <StatusBadge meta={ESTADO_META[e.state]} size="sm" /> : null}
      </div>
      <div className={styles.itemMeta}>
        {memoria ? <span><strong>{formatInt(e.count ?? 0)} lembranças</strong> (o conteúdo fica com a persona)</span> : null}
        {e.app ? <span>App: <span className={styles.mono}>{e.app}</span></span> : null}
        <span>{ORIGEM_LABEL[e.origin] ?? e.origin}</span>
        {!memoria ? <span>Evidência: {e.evidence.for} a favor · {e.evidence.against} contra</span> : null}
        {typeof e.uses === 'number' ? <span>Usos: {formatInt(e.uses)}</span> : null}
        {e.last_used_at ? <span>Último uso: {formatClock(e.last_used_at)}</span> : null}
        {detalhe ? <span>{detalhe}</span> : null}
        {espera ? <span className={styles.aviso}>Espera o dono: {espera}</span> : null}
        <span className={styles.mono}>{chaveDoItem(e)}</span>
      </div>
      {extra}
      {acoes.length > 0 && !aberta ? (
        <div className={styles.itemAcoes}>
          {acoes.map((a) => (
            <Button key={a.to} size="sm" variant={a.perigo ? 'dangerGhost' : a.to === 'published' || a.to === 'validated' ? 'primary' : 'secondary'}
                    onClick={() => setAberta(a)}>
              {a.label}
            </Button>
          ))}
        </div>
      ) : null}
      {aberta ? (
        <DecisaoInline
          acao={aberta}
          onCancelar={() => setAberta(null)}
          onConfirmar={async (motivo) => {
            const falha = await aplicarTransicao(e, aberta, motivo);
            if (!falha) {
              setAberta(null);
              onMudou();
            }
            return falha;
          }}
        />
      ) : null}
      {!memoria ? (
        <Disclosure bare summary="Evidência e trilha">
          {() => <DetalheDoItem entrada={e} />}
        </Disclosure>
      ) : null}
    </li>
  );
}
