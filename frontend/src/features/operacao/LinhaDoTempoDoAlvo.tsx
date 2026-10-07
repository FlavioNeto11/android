import { Badge } from '../../components/Badge';
import { formatInt } from '../../lib/format';
import { formatUsd } from '../usage/usage';
import styles from './Operacao.module.css';
import { commitEmPalavras, rotuloDoModelo, type CustoPorPasso, type SomaPorEstagio, type SomaPorModelo } from './custoPorPasso';
import { rotuloDoEstagioDoCusto } from './rotuloDoEstagioDoCusto';

const custo = (v: number | null) => (v === null ? 'não informado' : formatUsd(v));
const chamadas = (v: number | null) => (v === null ? '—' : formatInt(v));

/** O modelo em palavras, com o id cru no `title` (o rótulo é da tela, o dado é do servidor). */
export function NomeDoModelo({ id }: { id: string }) {
  return <span title={id}>{rotuloDoModelo(id)}</span>;
}

/** Somas por modelo e por estágio (de um agente ou da operação inteira): o que o 31.223 muda, em números. */
export function SomasDoCusto({ porModelo, porEstagio, rotulo }: { porModelo: readonly SomaPorModelo[]; porEstagio: readonly SomaPorEstagio[]; rotulo: string }) {
  if (porModelo.length === 0 && porEstagio.length === 0) return null;
  return (
    <div className={styles.somasDoCusto} data-somas-do-custo>
      {porModelo.length > 0 ? (
        <p className={styles.mudo} data-por-modelo>
          <strong>{rotulo}, por modelo:</strong>{' '}
          {porModelo.map((m, i) => <span key={m.modelo}>{i > 0 ? ' · ' : ''}<NomeDoModelo id={m.modelo} /> {chamadas(m.chamadas)} {m.chamadas === 1 ? 'chamada' : 'chamadas'}, {custo(m.custoUsd)}</span>)}
        </p>
      ) : null}
      {porEstagio.length > 0 ? (
        <p className={styles.mudo} data-por-estagio>
          <strong>{rotulo}, por estágio:</strong>{' '}
          {porEstagio.map((e, i) => <span key={e.estagio}>{i > 0 ? ' · ' : ''}{rotuloDoEstagioDoCusto(e.estagio)} {chamadas(e.chamadas)} {e.chamadas === 1 ? 'chamada' : 'chamadas'}, {custo(e.custoUsd)}</span>)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * 31.228 (parte 2): a linha do tempo de um agente por passo, na ordem em que rodaram: o estágio que o passo marca, o modelo que decidiu, o
 * commit (quem executou o efeito), as chamadas e o custo; depois as somas por modelo e por estágio e as chamadas sem etapa (planejamento).
 */
export function LinhaDoTempoDoAlvo({ dado }: { dado: CustoPorPasso }) {
  if (dado.passos.length === 0 && !dado.semPasso) return <p className={styles.mudo} data-modelos-vazio>Nenhuma chamada de IA registrada nesta execução.</p>;
  return (
    <div data-linha-do-tempo-do-alvo>
      {dado.passos.length > 0 ? (
        <table className={styles.tabelaDeModelos}>
          <caption className="sr-only">Passos do agente, com o modelo que decidiu e o custo de cada um</caption>
          <thead>
            <tr><th scope="col">Passo</th><th scope="col">Estágio</th><th scope="col">Decidiu</th><th scope="col">Commit</th><th scope="col">Chamadas</th><th scope="col">US$</th></tr>
          </thead>
          <tbody>
            {dado.passos.map((p) => {
              const commit = commitEmPalavras(p);
              return (
                <tr key={p.stepId} data-passo={p.chave} data-efeito={p.efeito ? 'sim' : 'nao'}>
                  <th scope="row">
                    <span className="mono">{p.seq !== null ? `${p.seq}. ` : ''}{p.chave}</span>
                    {p.efeito ? <> <Badge tone="warning" size="sm" title="A etapa tem efeito externo">com efeito</Badge></> : null}
                  </th>
                  <td>{p.estagio ? rotuloDoEstagioDoCusto(p.estagio) : <span className={styles.mudo}>—</span>}</td>
                  <td>{p.modelo ? <NomeDoModelo id={p.modelo} /> : <span className={styles.mudo}>só receita</span>}</td>
                  <td data-commit>{commit ?? <span className={styles.mudo}>—</span>}</td>
                  <td>{chamadas(p.chamadas)}</td>
                  <td>{custo(p.custoUsd)}</td>
                </tr>
              );
            })}
            {dado.semPasso ? (
              <tr data-sem-passo>
                <th scope="row" colSpan={4}>Planejamento e chamadas sem etapa</th>
                <td>{chamadas(dado.semPasso.chamadas)}</td><td>{custo(dado.semPasso.custoUsd)}</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      ) : null}
      <SomasDoCusto porModelo={dado.porModelo} porEstagio={dado.porEstagio} rotulo="Neste agente" />
    </div>
  );
}
