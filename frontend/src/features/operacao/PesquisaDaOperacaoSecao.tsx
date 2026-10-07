import { formatUsd4 } from '../../lib/format';
import { formatDateTime } from '../../lib/time';
import styles from './Operacao.module.css';
import { fonteComoLink, type Operacao } from './modelo';
import { ROTULO_DO_ESTADO_DA_PESQUISA } from './pesquisaDaOperacao';

const dominio = (url: string): string => { try { return new URL(url).hostname; } catch { return url; } };

/**
 * 31.234: a pesquisa da operação: reaproveitada do Livro (o critério, os fatos usados com origem e frescor) ou paga (o custo e as fontes
 * que ela achou). Sem o campo `pesquisa` (o central ainda não o manda) a tela só afirma o que ele já diz: o custo da pesquisa e as fontes;
 * nunca "reaproveitada" por dedução.
 */
export function PesquisaDaOperacaoSecao({ op }: { op: Operacao }) {
  const p = op.pesquisa ?? null;
  const fontes = op.fontes_da_pesquisa ?? [];
  const custoPago = p?.custoUsd ?? op.custo?.pesquisa_usd ?? null;
  const temEstado = p?.estado != null;
  if (!temEstado && fontes.length === 0 && custoPago === null && !op.assunto) return null;
  const reaproveitada = p?.estado === 'reaproveitada_do_livro';

  return (
    <section aria-label="Pesquisa da operação" className={styles.faixa} data-pesquisa-da-operacao={p?.estado ?? 'sem_estado'}>
      <p className={styles.objetivo}>
        <strong>Pesquisa da operação:</strong>{' '}
        {temEstado ? ROTULO_DO_ESTADO_DA_PESQUISA[p!.estado!]
          : custoPago !== null && custoPago > 0 ? `paga, ${formatUsd4(custoPago)}`
            : fontes.length > 0 ? 'fontes achadas'
              : 'sem custo de pesquisa registrado'}
        {temEstado && !reaproveitada && custoPago !== null ? <span className={styles.mudo}> ({formatUsd4(custoPago)})</span> : null}
        {reaproveitada ? <span className={styles.mudo}> (custo da pesquisa: {formatUsd4(custoPago ?? 0)})</span> : null}
        .
      </p>
      {!temEstado ? (
        <p className={styles.mudo} data-sem-estado-da-pesquisa>O central ainda não diz se o Livro bastou ou se a pesquisa foi paga; o que aparece aqui é só o que ele registra.</p>
      ) : null}
      {reaproveitada ? (
        <div data-pesquisa-reaproveitada>
          {p!.criterio ? <p className={styles.mudo}>Critério: {p!.criterio}{p!.minimoDeFatos !== null ? ` (mínimo de ${p!.minimoDeFatos} ${p!.minimoDeFatos === 1 ? 'fato' : 'fatos'})` : ''}.</p> : null}
          {p!.frescorAte ? <p className={styles.mudo} data-frescor>Vale até {formatDateTime(p!.frescorAte)}: o primeiro fato que vence reabre a pesquisa paga.</p> : null}
          {p!.fatos.length > 0 ? (
            <ul className={styles.motivos} aria-label="Fatos do Livro usados">
              {p!.fatos.map((f) => (
                <li key={f.item} data-fato-do-livro={f.item}>
                  <span className="mono">{f.item}</span>
                  <span className={styles.mudo}>
                    {f.origem ? ` · origem ${f.origem}` : ''}{f.confianca ? ` · ${f.confianca === 'confirmado' ? 'confirmado' : 'hipótese'}` : ''}{f.frescorAte ? ` · vale até ${formatDateTime(f.frescorAte)}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          ) : <p className={styles.mudo}>O central não listou os fatos usados.</p>}
        </div>
      ) : null}
      {fontes.length > 0 ? (
        <p className={styles.objetivo} data-fontes-da-pesquisa>
          <strong>Fontes que a pesquisa achou:</strong>{' '}
          {fontes.map((f, i) => {
            const link = fonteComoLink(f);
            return <span key={`${f}-${i}`}>{i > 0 ? ' · ' : ''}{link ? <a className={styles.link} href={link} target="_blank" rel="noopener noreferrer">{dominio(f)}</a> : dominio(f)}</span>;
          })}
        </p>
      ) : null}
    </section>
  );
}
