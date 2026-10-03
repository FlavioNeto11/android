import { MessageCircleQuestion } from 'lucide-react';
import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { toApiError } from '../../api/client';
import { Button } from '../../components/Button';
import { Disclosure } from '../../components/Disclosure';
import { EmptyState } from '../../components/EmptyState';
import { TextInput } from '../../components/Field';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { LoadErrorBanner, toLoadError, type LoadError } from '../../lib/loadError';
import { formatDateTime, formatQuando } from '../../lib/time';
import { toast } from '../../store/toasts';
import { useUiStore } from '../../store/ui';
import {
  LIMITE_SEM_FILTRO, NENHUM, type PerguntaDeIntencao, type PerguntasDeIntencao, apiIntencao, filtrarCandidatos,
} from './intencao';
import styles from './Aprendizado.module.css';

function abrirExecucao(runId: string) {
  useUiStore.getState().selectRun(runId);
  useUiStore.getState().setView('execucoes');
}

/** Uma pergunta: o comando, onde e quando, e as opções (todas as do catálogo daquela hora, mais "Nenhuma destas"). */
function Pergunta({ p, onRespondida }: { p: PerguntaDeIntencao; onRespondida: () => void }) {
  const [escolha, setEscolha] = useState<string | null>(null);
  const [termo, setTermo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const id = useId();
  const visiveis = useMemo(() => filtrarCandidatos(p.candidatos, termo), [p.candidatos, termo]);
  const nomeDaEscolha = escolha === NENHUM ? 'Nenhuma destas' : p.candidatos.find((c) => c.skill_id === escolha)?.nome;

  const responder = async () => {
    if (!escolha) return;
    setEnviando(true);
    setErro(null);
    try {
      await apiIntencao.responder(p.run_id, escolha);
      toast({ tone: 'success', title: 'Resposta registrada', details: [`“${p.comando ?? p.run_id}” → ${nomeDaEscolha ?? escolha}`] });
      onRespondida();
    } catch (err) {
      const x = toApiError(err);
      if (x.status === 409 || x.status === 404) {
        toast({ tone: 'warning', title: 'Esta pergunta já foi respondida', details: ['A lista foi atualizada.'] });
        onRespondida();
      } else {
        setErro(toLoadError(err).message);
      }
    } finally {
      setEnviando(false);
    }
  };

  return (
    <li className={styles.item} data-item={`intencao:${p.run_id}`}>
      <div className={styles.itemHead}>
        <span className={styles.itemTitulo}>{p.comando ? `“${p.comando}”` : 'Comando indisponível'}</span>
      </div>
      <div className={styles.itemMeta}>
        {p.app ? <span title={p.app}>{p.app_nome ?? p.app}</span> : null}
        {p.terminou_em ? <span title={formatDateTime(p.terminou_em)}>terminou {formatQuando(p.terminou_em)}</span> : null}
        <span>Execução: <button type="button" className={styles.linkBtn} onClick={() => abrirExecucao(p.run_id)}>{p.run_id}</button></span>
      </div>
      <fieldset className={styles.intencaoOpcoes} aria-describedby={erro ? `${id}-erro` : undefined}>
        <legend className={styles.intencaoLegenda}>Qual destas era a intenção do pedido?</legend>
        {p.candidatos.length > LIMITE_SEM_FILTRO ? (
          <TextInput small value={termo} onChange={(e) => setTermo(e.target.value)} placeholder="Filtrar por nome"
                     aria-label="Filtrar as opções por nome" className={styles.intencaoFiltro} />
        ) : null}
        <div className={styles.intencaoLista}>
          {visiveis.map((c) => (
            <label key={c.skill_id} className={styles.intencaoOpcao} title={c.skill_id}>
              <input type="radio" name={`intencao-${p.run_id}`} value={c.skill_id} checked={escolha === c.skill_id}
                     onChange={() => setEscolha(c.skill_id)} />
              <span>{c.nome}</span>
            </label>
          ))}
          {visiveis.length === 0 ? <span className={styles.secaoLead}>Nenhuma opção com esse nome.</span> : null}
        </div>
        <label className={styles.intencaoOpcao}>
          <input type="radio" name={`intencao-${p.run_id}`} value={NENHUM} checked={escolha === NENHUM}
                 onChange={() => setEscolha(NENHUM)} />
          <span>Nenhuma destas <span className={styles.intencaoDica}>— o pedido era outra coisa</span></span>
        </label>
      </fieldset>
      {erro ? <p id={`${id}-erro`} role="alert" className={styles.intencaoErro}>{erro}</p> : null}
      <div className={styles.itemAcoes}>
        <Button size="sm" variant="primary" loading={enviando} onClick={() => void responder()}
                disabledReason={escolha ? null : 'Escolha uma opção.'}>
          Responder
        </Button>
        {escolha && nomeDaEscolha ? <span className={styles.secaoLead}>Escolhida: {nomeDaEscolha}</span> : null}
      </div>
    </li>
  );
}

/**
 * "Qual era o pedido?" (item 30.25): execuções reais que deram certo, com prova, sem que o sistema reconhecesse o
 * pedido. A resposta da pessoa vira exemplo para o reconhecimento do pedido; nada é executado de novo. Opcional: nada
 * fica esperando por ela, por isso não entra na contagem do "Para aprovar".
 */
export function IntencaoSecao() {
  const [dados, setDados] = useState<PerguntasDeIntencao | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);

  const carregar = useCallback(async () => {
    try {
      setDados(await apiIntencao.pendentes());
      setErro(null);
    } catch (err) {
      setErro(toLoadError(err));
    }
  }, []);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  return (
    <section className={styles.secao} aria-labelledby="aprendizado-intencao">
      <h2 id="aprendizado-intencao" className={styles.secaoTitulo}>
        <MessageCircleQuestion size={16} aria-hidden /> Qual era o pedido?
      </h2>
      <p className={styles.secaoLead}>
        Pedidos que deram certo sem que o sistema reconhecesse qual habilidade eles pediam. Dizer qual era ajuda o sistema
        a reconhecer da próxima vez. É opcional: nada fica parado esperando a resposta.
      </p>
      <Disclosure summary="Saiba mais" bare>
        <p className={styles.secaoLead}>
          Só entram execuções reais em que todas as etapas foram comprovadas. As opções são as habilidades e os fluxos que
          o sistema conhecia quando a execução terminou, em ordem alfabética e sem o palpite dele: a resposta é só sua.
          Responder não executa nada de novo nem muda nenhuma habilidade.
        </p>
      </Disclosure>
      {erro ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
      {dados === null && !erro ? (
        <LoadingRegion label="Carregando os pedidos para identificar…">
          <Skeleton height={96} radius={8} />
        </LoadingRegion>
      ) : null}
      {dados !== null && dados.itens.length === 0 ? (
        <EmptyState icon={MessageCircleQuestion} compact title="Nenhum pedido para identificar">
          Quando uma execução der certo sem que o sistema reconheça o pedido, ela aparece aqui.
        </EmptyState>
      ) : null}
      {dados !== null && dados.itens.length > 0 ? (
        <>
          <ul className={styles.lista} aria-label="Pedidos para identificar">
            {dados.itens.map((p) => <Pergunta key={p.review_id} p={p} onRespondida={() => void carregar()} />)}
          </ul>
          {dados.total > dados.itens.length ? (
            <p className={styles.secaoLead}>Mostrando {dados.itens.length} de {dados.total}. Responda estes para ver os próximos.</p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
