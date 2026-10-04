import { CircleCheck, CircleOff, MessageSquare, Radio, Send, SquareKanban, TriangleAlert, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { CanaisEstado, CanalAvisoTelegram, CanalConversaTelegram, CanalTrello } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Banner } from '../../components/Banner';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { type LoadError, LoadErrorBanner, LoadErrorState, toLoadError } from '../../lib/loadError';
import { useIntervaloVisivel } from '../../lib/polling';
import { tempoRelativo, useNow } from '../../lib/time';
import {
  falhaVigente, frasesDaFila, frasesDasEntradas, frasesDosCartoes, MOTIVO_DA_FALHA, seloDaConversa, seloDoAviso, seloDoTrello,
  textoDoProblema, type Selo,
} from './resumo';
import styles from './Canais.module.css';

/** Relê a cada 30 s (com a aba visível): o estado dos canais anda devagar, e a tela não escreve nada. */
const RELEITURA_MS = 30_000;

const SELO: Record<Selo, { rotulo: string; tom: 'success' | 'muted' | 'warning'; icone: LucideIcon }> = {
  ligado: { rotulo: 'Ligado', tom: 'success', icone: CircleCheck },
  desligado: { rotulo: 'Desligado', tom: 'muted', icone: CircleOff },
  problema: { rotulo: 'Com problema', tom: 'warning', icone: TriangleAlert },
};

function Fato({ nome, children }: { nome: string; children: ReactNode }) {
  return (
    <div className={styles.fato}>
      <dt>{nome}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** "há 3 min" ou, sem registro, o que significa não haver: o traço sozinho não diz se nunca houve ou se não foi lido. */
function Quando({ iso, agora, nunca }: { iso: string | null; agora: number; nunca: string }) {
  return iso ? <span title={iso}>{tempoRelativo(iso, agora)}</span> : <span className={styles.nunca}>{nunca}</span>;
}

function CartaoDoCanal({ titulo, subtitulo, icone: Icone, selo, desligadoDiz, problemas, children }: {
  titulo: string;
  subtitulo: string;
  icone: LucideIcon;
  selo: Selo;
  desligadoDiz: string;
  problemas: string[];
  children: ReactNode;
}) {
  const s = SELO[selo];
  return (
    <Card role="region" aria-label={titulo} className={styles.cartao}>
      <CardHeader
        title={<span className={styles.titulo}><Icone size={16} aria-hidden />{titulo}</span>}
        subtitle={subtitulo}
        actions={<Badge tone={s.tom} icon={s.icone}>{s.rotulo}</Badge>}
        level={2}
      />
      <CardBody className={styles.corpo}>
        {selo === 'desligado' ? <p className={styles.desligado}>{desligadoDiz}</p> : null}
        {problemas.length > 0 ? (
          <Banner tone="warning" icon={TriangleAlert} compact title="O que a Central encontrou">
            <ul className={styles.problemas}>
              {problemas.map((p) => <li key={p}>{textoDoProblema(p)}</li>)}
            </ul>
          </Banner>
        ) : null}
        <dl className={styles.fatos}>{children}</dl>
      </CardBody>
    </Card>
  );
}

function AvisoTelegram({ d, agora }: { d: CanalAvisoTelegram; agora: number }) {
  const selo = seloDoAviso(d);
  const problemas = d.problemas;
  const semSegredo = d.ligado && !d.segredo_presente && !problemas.includes('avisos_sem_segredo');
  return (
    <CartaoDoCanal
      titulo="Aviso pelo Telegram"
      subtitulo="Empurra para o seu celular o que espera uma decisão."
      icone={Send}
      selo={selo}
      desligadoDiz="Desligado na configuração: nenhum aviso sai da Central. As pendências seguem na caixa do painel."
      problemas={semSegredo ? ['avisos_sem_segredo', ...problemas] : problemas}
    >
      <Fato nome="Avisos">{frasesDaFila(d.fila)}</Fato>
      <Fato nome="Último envio"><Quando iso={d.ultimo_envio_em} agora={agora} nunca="nenhum aviso enviado ainda" /></Fato>
      <Fato nome="Última falha">
        {d.ultima_falha ? (
          <span className={falhaVigente(d) ? styles.alerta : undefined}>
            {MOTIVO_DA_FALHA[d.ultima_falha.motivo] ?? MOTIVO_DA_FALHA.outro}, {tempoRelativo(d.ultima_falha.em, agora)}
            {falhaVigente(d) ? '' : ' (depois disso um aviso saiu)'}
          </span>
        ) : <span className={styles.nunca}>nenhuma</span>}
      </Fato>
    </CartaoDoCanal>
  );
}

function ConversaTelegram({ d, agora }: { d: CanalConversaTelegram; agora: number }) {
  return (
    <CartaoDoCanal
      titulo="Conversa pelo Telegram"
      subtitulo="Você responde, aprova e pergunta pelo mesmo bot."
      icone={MessageSquare}
      selo={seloDaConversa(d)}
      desligadoDiz="Desligada na configuração: a Central só envia avisos, não lê o que você escreve no Telegram."
      problemas={d.problemas}
    >
      <Fato nome="Mensagens">{frasesDasEntradas(d.entradas)}</Fato>
      <Fato nome="Última mensagem recebida"><Quando iso={d.ultima_leitura_em} agora={agora} nunca="nenhuma ainda" /></Fato>
    </CartaoDoCanal>
  );
}

function Trello({ d, agora }: { d: CanalTrello; agora: number }) {
  return (
    <CartaoDoCanal
      titulo="Trello"
      subtitulo="O quadro espelha o plano e o estado das frentes."
      icone={SquareKanban}
      selo={seloDoTrello(d)}
      desligadoDiz="Desligado na configuração: a Central não cria nem lê cartões."
      problemas={d.problemas}
    >
      <Fato nome="Cartões">{frasesDosCartoes(d.cartoes)}</Fato>
      <Fato nome="Última leitura do quadro"><Quando iso={d.ultima_reconciliacao_em} agora={agora} nunca="ainda não leu" /></Fato>
      <Fato nome="Webhook">
        {d.webhook_ligado ? 'ligado (o Trello avisa na hora)' : 'desligado (a leitura periódica cobre)'}
        {d.webhook_ligado ? `; cadastro automático ${d.cadastro_automatico ? 'ligado' : 'desligado'}` : ''}
      </Fato>
      <Fato nome="Comentários e movimentos">{frasesDasEntradas(d.entradas, 'Nenhum recebido ainda.')}</Fato>
    </CartaoDoCanal>
  );
}

export function CanaisPage() {
  const [dados, setDados] = useState<CanaisEstado | null>(null);
  const [erro, setErro] = useState<LoadError | null>(null);
  const token = useRef(0);
  const agora = useNow();

  const carregar = useCallback(async () => {
    const meu = ++token.current;
    try {
      const r = await api.canaisEstado();
      if (meu !== token.current) return;
      setDados(r);
      setErro(null);
    } catch (e) {
      if (meu !== token.current) return;
      setErro(toLoadError(e));
    }
  }, []);

  useEffect(() => {
    void carregar();
    return () => { token.current += 1; };
  }, [carregar]);
  useIntervaloVisivel(carregar, RELEITURA_MS);

  let corpo: ReactNode;
  if (dados) {
    corpo = (
      <>
        {erro ? <LoadErrorBanner error={erro} onRetry={() => void carregar()} /> : null}
        <div className={styles.grade}>
          <AvisoTelegram d={dados.aviso_telegram} agora={agora} />
          <ConversaTelegram d={dados.conversa_telegram} agora={agora} />
          <Trello d={dados.trello} agora={agora} />
        </div>
      </>
    );
  } else if (erro) {
    corpo = <LoadErrorState what="o estado dos canais" error={erro} onRetry={() => void carregar()} />;
  } else {
    corpo = <LoadingRegion label="Carregando os canais…"><Skeleton height={220} /></LoadingRegion>;
  }

  return (
    <Page
      title="Canais"
      lead={<><Radio size={14} aria-hidden className={styles.leadIcone} /> Como andam o aviso e a conversa pelo Telegram e o espelho no Trello. Só leitura: ligar, desligar e reenviar ficam na configuração da instalação.</>}
    >
      {corpo}
    </Page>
  );
}
