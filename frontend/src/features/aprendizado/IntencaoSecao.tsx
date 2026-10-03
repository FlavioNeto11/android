import { MessageCircleQuestion, SkipForward } from 'lucide-react';
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

/** Uma pergunta: o comando, onde e quando, e as opções (todas as do catálogo daquela hora, mais "Nenhuma destas").
 *  `posicao`: "1 de 4"; `onPular` só existe quando há outra pergunta para mostrar. */
function Pergunta({ p, posicao, onRespondida, onPular }: {
  p: PerguntaDeIntencao; posicao: string; onRespondida: () => void; onPular: (() => void) | null;
}) {
  const [escolha, setEscolha] = useState<string | null>(null);
  const [termo, setTermo] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const id = useId();
  const visiveis = useMemo(() => filtrarCandidatos(p.candidatos, termo), [p.candidatos, termo]);
  // Dois fluxos podem ter o mesmo nome (o catálogo de 03/10 tem): sem o id, a pessoa não teria como distingui-los.
  const repetidos = useMemo(() => {
    const vezes = new Map<string, number>();
    for (const c of p.candidatos) vezes.set(c.nome, (vezes.get(c.nome) ?? 0) + 1);
    return new Set([...vezes].filter(([, n]) => n > 1).map(([nome]) => nome));
  }, [p.candidatos]);
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
      } else if (x.status === 422) {
        // O texto do servidor fala da cadeia de resolução; para a pessoa basta saber o que fazer.
        setErro('Essa opção não está entre as desta pergunta. Escolha outra ou "Nenhuma destas".');
      } else {
        setErro(toLoadError(err).message);
      }
    } finally {
      setEnviando(false);
    }
  };

  return (
    <div className={styles.item} data-item={`intencao:${p.run_id}`}>
      <div className={styles.itemHead}>
        <span className={`${styles.itemTitulo} ${styles.intencaoComando}`}>
          {p.comando ? `“${p.comando}”` : 'Comando indisponível'}
        </span>
        <span className={styles.secaoLead}>{posicao}</span>
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
            <label key={c.skill_id} className={styles.intencaoOpcao}
                   title={repetidos.has(c.nome) ? `${c.nome} (${c.skill_id})` : c.nome}>
              <input type="radio" name={`intencao-${p.run_id}`} value={c.skill_id} checked={escolha === c.skill_id}
                     onChange={() => setEscolha(c.skill_id)} />
              <span className={styles.intencaoTexto}>
                <span className={styles.intencaoNome}>{c.nome}</span>
                {repetidos.has(c.nome) ? <span className={styles.intencaoDica}>{c.skill_id}</span> : null}
              </span>
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
        {onPular ? (
          <Button size="sm" variant="ghost" icon={SkipForward} onClick={onPular} disabled={enviando}>Pular</Button>
        ) : null}
        {escolha && nomeDaEscolha ? <span className={styles.secaoLead}>Escolhida: {nomeDaEscolha}</span> : null}
      </div>
    </div>
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
  // Uma pergunta por vez (o catálogo inteiro em cada uma deixaria a aba enorme). "Pular" só muda a da vez, aqui: a
  // pulada continua aberta no servidor e volta depois das outras.
  const [vez, setVez] = useState(0);

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
      {dados !== null && dados.itens.length > 0 ? (() => {
        const n = dados.itens.length;
        const i = vez % n;
        const p = dados.itens[i];
        if (!p) return null;
        const posicao = `${i + 1} de ${dados.total > n ? `${n} (${dados.total} no total)` : n}`;
        return (
          <Pergunta key={p.review_id} p={p} posicao={posicao} onRespondida={() => void carregar()}
                    onPular={n > 1 ? () => setVez((v) => (v + 1) % n) : null} />
        );
      })() : null}
    </section>
  );
}
