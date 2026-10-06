/**
 * 31.132: depois de salvar (e na sessão já salva), a pessoa chega ao fluxo e vê o estado dele. Antes o resultado dizia
 * "Fluxo f-… salvo" com o id cru, sem link e sem dizer em que estado o fluxo ficou nem o que fazer a seguir: a sessão
 * aparecia como "salvo" e o Livro mostrava o mesmo fluxo como Desligado. Aqui o estado vem do Livro (só leitura, o mesmo
 * `GET /api/aprendizado/fluxo/{id}`), o próximo passo sai dele e o link leva ao item (`?item=fluxo:<id>`).
 */
import { useEffect, useState } from 'react';
import { toApiError } from '../../api/client';
import { SeloEmProva } from '../../components/SeloEmProva';
import { SeloNascidoDeProva } from '../../components/SeloNascidoDeProva';
import { textoDaEsperaDaPessoa } from '../../lib/emProva';
import { hashDe } from '../../lib/rotas';
import { apiAprendizado } from '../aprendizado/api';
import { rotuloDoEstado, type EntradaDoLivro } from '../aprendizado/model';
import styles from './Training.module.css';

/** O endereço do item do fluxo no Livro (Aprendido). */
export const linkDoFluxo = (flowId: string): string => hashDe('aprendizado', { query: { aba: 'aprendido', item: `fluxo:${flowId}` } });

/** O que fazer a seguir, pelo estado em que o Livro tem o fluxo agora; vazio quando não há o que dizer. */
export function proximoPassoDoFluxo(e: Pick<EntradaDoLivro, 'state' | 'ensinado_em_prova' | 'espera_a_pessoa'>): string {
  if (e.state === 'disabled') return 'Está desligado: só uma pessoa o reativa, no Livro.';
  if (e.state === 'deprecated') return 'Está aposentado: não vale mais para nenhum comando.';
  if (e.state === 'candidate' || e.state === 'validated') return 'Ainda não está publicado: espera a aprovação, em Aprendizado → Para aprovar.';
  if (e.state === 'published') {
    if (e.espera_a_pessoa) return `${textoDaEsperaDaPessoa(e.espera_a_pessoa)} Abra no Livro e use “Confirmar que fica” para liberá-lo.`;
    if (e.ensinado_em_prova) return 'Falta a prova: acompanhe no Livro, onde o selo “em prova” sai quando uma prova real der certo ou uma pessoa confirmar que fica.';
    return 'Já vale para quem está no escopo escolhido.';
  }
  return '';
}

export function FluxoNoLivro({ flowId }: { flowId: string }) {
  const [entrada, setEntrada] = useState<EntradaDoLivro | null>(null);
  const [lido, setLido] = useState<'lendo' | 'ok' | 'sumiu' | 'falhou'>('lendo');

  useEffect(() => {
    const ctl = new AbortController();
    setEntrada(null);
    setLido('lendo');
    apiAprendizado.detalhe('fluxo', flowId, ctl.signal)
      .then((d) => { setEntrada(d.item); setLido('ok'); })
      .catch((e: unknown) => { if (!ctl.signal.aborted) setLido(toApiError(e).status === 404 ? 'sumiu' : 'falhou'); });
    return () => ctl.abort();
  }, [flowId]);

  const link = <a className={styles.linkDoLivro} href={linkDoFluxo(flowId)}>Abrir no Livro</a>;
  if (lido === 'sumiu') return <p className={styles.hint} role="status">Este fluxo não está mais no Livro (foi apagado depois de salvar).</p>;
  if (lido === 'lendo') return <p className={styles.hint} role="status">Lendo o estado do fluxo no Livro…</p>;
  if (lido === 'falhou' || !entrada) {
    return <p className={styles.hint} role="status">Não deu para ler o estado do fluxo agora; o Livro mostra. {link}</p>;
  }
  return (
    <p className={styles.noLivro} role="status" aria-label="Estado do fluxo no Livro">
      <span>No Livro agora: <strong>{rotuloDoEstado(entrada.state)}</strong></span>
      <SeloEmProva ensinado={entrada.ensinado_em_prova} />
      <SeloNascidoDeProva nascido={entrada.nascido_de_prova} />
      <span className={styles.muted}>{proximoPassoDoFluxo(entrada)}</span>
      {link}
    </p>
  );
}
