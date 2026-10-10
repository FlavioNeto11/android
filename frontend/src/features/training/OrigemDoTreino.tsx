/**
 * 31.111 F5 (adendo v1.75): o treino que nasceu de uma etapa que falhou. O selo diz de onde a sessão veio (na barra, em
 * "Para revisar", na revisão e no relatório do salvar), e o contexto mostra o que a execução fez, o que a etapa esperava,
 * a tentativa que falhou e as imagens. Só leitura: nada daqui roda sozinho nem reabre a execução.
 *
 * 31.116: o diagnóstico da falha (`origin.diagnostico`) aparece aqui, acima do contexto: a causa provável em palavras, o que
 * mostrar ao gravar e os fatos que sustentam a hipótese. É uma sugestão: não mexe na intenção da sessão.
 */
import { Compass, Wrench } from 'lucide-react';
import { evidenceUrl } from '../../api/client';
import type { TrainingDiagnostico, TrainingOrigin } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Disclosure } from '../../components/Disclosure';
import { hashDe } from '../../lib/rotas';
import { STEP_STATUS, metaOf } from '../../lib/status';
import styles from './Training.module.css';

/** O link da execução de origem (`#/execucoes/<run_id>`). */
export const linkDaExecucao = (origin: Pick<TrainingOrigin, 'run_id'>): string => hashDe('execucoes', { segmentos: [origin.run_id] });

/**
 * 31.119: o selo abre a execução de origem. `aoAbrir` deixa quem está dentro de um diálogo (a revisão) decidir se sai antes
 * de navegar; sem ele o link é o de sempre.
 */
export function SeloDeOrigem({ origin, aoAbrir }: { origin: TrainingOrigin; aoAbrir?: (href: string) => void }) {
  const href = linkDaExecucao(origin);
  return (
    <a href={href} className={styles.seloLink} title={`Abrir a execução ${origin.run_id} (etapa ${origin.step_key})`}
       onClick={aoAbrir ? (e) => { e.preventDefault(); aoAbrir(href); } : undefined}>
      {origin.exploracao ? (
        <Badge size="sm" tone="info" icon={Compass}>
          ensina onde a IA parou<span className="sr-only"> na exploração da etapa {origin.step_key} da execução {origin.run_id}: abrir a execução</span>
        </Badge>
      ) : (
        <Badge size="sm" tone="info" icon={Wrench}>
          corrige uma falha<span className="sr-only"> da etapa {origin.step_key} da execução {origin.run_id}: abrir a execução</span>
        </Badge>
      )}
    </a>
  );
}

/** `tipo_sem_regra` -> "tipo sem regra": o código dos fatos é snake_case, e a pessoa lê palavras. */
const palavrasDoCodigo = (codigo: string): string => codigo.replace(/_/g, ' ');

function DiagnosticoDaFalha({ diagnostico }: { diagnostico: TrainingDiagnostico }) {
  const conhecida = diagnostico.causa !== 'indeterminada';
  return (
    <div className={styles.diagnostico} role="group" aria-label="Diagnóstico da falha">
      <p className={styles.diagnosticoCausa}>
        <strong>{conhecida ? 'Causa provável' : 'Causa'}:</strong> {conhecida ? diagnostico.rotulo : 'não deu para saber'}
      </p>
      <p className={styles.hint}><strong>O que mostrar:</strong> {diagnostico.pergunta}</p>
      {diagnostico.fatos.length ? (
        <Disclosure summary="Por que a plataforma acha isso" bare>
          <dl className={styles.fatos}>
            {diagnostico.fatos.map((f, i) => (
              <div key={`${f.codigo}-${i}`}>
                <dt>{palavrasDoCodigo(f.codigo)}</dt>
                <dd>{f.valor}</dd>
              </div>
            ))}
          </dl>
        </Disclosure>
      ) : null}
    </div>
  );
}

export function OrigemDoTreino({ origin, aoAbrirExecucao }: { origin: TrainingOrigin; aoAbrirExecucao?: (href: string) => void }) {
  const ctx = origin.context;
  return (
    <section className={styles.origem} aria-label="Origem do treino">
      <p className={styles.recLine}>
        <SeloDeOrigem origin={origin} aoAbrir={aoAbrirExecucao} />
        <span>Etapa <span className="mono">{origin.step_key}</span> da execução <span className="mono">{origin.run_id}</span></span>
      </p>
      {/* 31.120: só ids (execução, etapa, tentativa), para achar a falha no resto do painel e na API. */}
      <p className={styles.hint}>
        Etapa <span className="mono">{origin.step_id}</span>
        {origin.attempt_id ? <> · tentativa <span className="mono">{origin.attempt_id}</span></> : null}
      </p>
      {origin.exploracao ? (
        <p className={styles.hint} role="note">
          A IA explorou o app para este pedido e não chegou lá. O que você ensina aqui parte da tela em que ela parou e serve a todas as personas.
        </p>
      ) : null}
      {origin.motivo
        ? <p className={styles.hint}>Motivo: {origin.motivo}</p>
        : <p className={styles.hint}>A etapa já não existe (a limpeza de execuções antigas a apagou): só os ids ficaram como rótulo.</p>}
      {origin.diagnostico ? <DiagnosticoDaFalha diagnostico={origin.diagnostico} /> : null}
      {ctx?.disponivel ? (
        <Disclosure summary="O que a execução fez, o esperado e as imagens" bare>
          <div className={styles.origemCorpo}>
            {ctx.trilha?.length ? (
              <>
                <h4 className={styles.sub}>O que a execução fez neste aparelho</h4>
                <ol className={styles.trilha}>
                  {ctx.trilha.map((t) => (
                    <li key={t.step_id} className={t.falhou ? styles.trilhaFalhou : undefined} aria-current={t.falhou ? 'step' : undefined}>
                      <strong>{t.titulo}</strong> · {metaOf(STEP_STATUS, t.status).label}
                      {t.falhou ? <> · <em>a que falhou</em></> : null}
                      {t.motivo ? <span className={styles.muted}>: {t.motivo}</span> : null}
                    </li>
                  ))}
                </ol>
              </>
            ) : null}
            {ctx.esperado ? (
              <p className={styles.hint}>
                {origin.exploracao ? 'Pedido de origem' : 'A etapa esperava'}: {ctx.esperado.description || ctx.esperado.value || ctx.esperado.kind}
                {ctx.esperado.description && ctx.esperado.value ? <> (<span className="mono">{ctx.esperado.value}</span>)</> : null}
              </p>
            ) : null}
            {ctx.tentativa ? (
              <p className={styles.hint}>
                Tentativa {ctx.tentativa.number} ({ctx.tentativa.status})
                {ctx.tentativa.failure_kind ? <> · {ctx.tentativa.failure_kind}</> : null}
                {ctx.tentativa.strategy ? <> · estratégia {ctx.tentativa.strategy}</> : null}
                {ctx.tentativa.erro ? <>: {ctx.tentativa.erro}</> : null}
                {ctx.tentativa.failure_screen ? <> · tela: {ctx.tentativa.failure_screen}</> : null}
              </p>
            ) : null}
            {ctx.evidencias?.length ? (
              <ul className={styles.evidencias} aria-label="Imagens da falha">
                {ctx.evidencias.map((e) => (
                  <li key={e.id}>
                    {e.disponivel
                      ? <img src={evidenceUrl({ id: e.id, url: null })} alt={e.nota ?? `Evidência ${e.id}`} loading="lazy" decoding="async" />
                      : <span className={styles.muted}>{e.nota ?? `Evidência ${e.id}`}: não disponível (redigida)</span>}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </Disclosure>
      ) : ctx ? (
        <p className={styles.hint}>O contexto da execução já não está disponível.</p>
      ) : null}
    </section>
  );
}
