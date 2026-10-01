import { CircleCheck, OctagonAlert, TriangleAlert, type LucideIcon } from 'lucide-react';
import { Popover } from '../../components/Popover';
import { Skeleton } from '../../components/Skeleton';
import { toneClass } from '../../components/tone';
import { cx } from '../../lib/format';
import { hashDe } from '../../lib/rotas';
import { focarConteudo } from '../../lib/scroll';
import type { Tone } from '../../lib/status';
import { useAppStore } from '../../store/app';
import {
  ROTULO_DO_NIVEL, personasBloqueadas, useSaudeDoAmbiente, type Motivo, type NivelDoAmbiente,
} from '../../store/metricas';
import { usePendencias } from '../pendencias/usePendencias';
import { usePersonas } from '../profiles/usePersonas';
import styles from './SaudeAmbiente.module.css';

const VISUAL: Record<NivelDoAmbiente, { tone: Tone; icon: LucideIcon }> = {
  ok: { tone: 'success', icon: CircleCheck },
  atencao: { tone: 'warning', icon: TriangleAlert },
  critico: { tone: 'danger', icon: OctagonAlert },
};

function motivos(n: number): string {
  return `${n} ${n === 1 ? 'motivo' : 'motivos'}`;
}

/**
 * Semáforo do ambiente (revisão de UX, tarefa 02). Substitui a pílula que só repetia `GET /health` — ela ficava
 * verde com o notebook da LAN fora do ar. O nível e os motivos saem de `store/metricas`, a mesma conta das telas;
 * cada motivo leva à tela que resolve. O cabeçalho só resume: servidores e vagas são da Infraestrutura; SDK,
 * Appium e custo de IA, do Diagnóstico (antes o popover repetia os três).
 */
export function SaudeAmbiente() {
  const hydrated = useAppStore((s) => s.hydrated);
  const health = useAppStore((s) => s.health);
  const { nivel, motivos: lista } = useSaudeDoAmbiente();

  if (!hydrated && !health) return <Skeleton width={118} height={28} radius={999} />;
  const { tone, icon: Icon } = VISUAL[nivel];
  const rotulo = ROTULO_DO_NIVEL[nivel];

  return (
    <Popover
      // O nome acessível começa pelo que está escrito no botão (rótulo e contagem), e só então diz o que ele faz.
      // RF-44 (WCAG 2.5.3): literalmente — "Ambiente crítico 8 motivos…" contém o "Ambiente crítico 8" visível; com a
      // vírgula e sem espaço entre o rótulo e o selo, o texto lido era "Ambiente crítico8" e não estava no nome.
      label={`${rotulo}${lista.length > 0 ? ` ${motivos(lista.length)}` : ''}. Abrir detalhes do ambiente`}
      title="Saúde do ambiente"
      align="start"
      triggerClassName={cx(styles.gatilho, toneClass(tone))}
      trigger={
        <>
          <Icon size={14} aria-hidden />
<span className={styles.rotulo}>{rotulo}</span>
          {/* O espaço separa rótulo e selo no TEXTO do botão; entre itens flex ele não ocupa lugar na tela. */}
          {lista.length > 0 ? <>{' '}<span className={styles.contagem}>{lista.length}</span></> : null}
        </>
      }
    >
      {(close) => <Detalhes nivel={nivel} lista={lista} versao={health?.version ?? null} fechar={close} />}
    </Popover>
  );
}

/** Só existe com o popover aberto: a leitura de personas não roda a cada snapshot no cabeçalho. */
function Detalhes({ nivel, lista, versao, fechar }: {
  nivel: NivelDoAmbiente; lista: Motivo[]; versao: string | null; fechar: () => void;
}) {
  const bloqueadas = personasBloqueadas(usePersonas());
  // D1: o mesmo total da caixa de Pendências (e do chip do topo e do selo do menu), levando a ela.
  const aguardando = usePendencias().total;
  // RF-45: o link troca a tela e o popover some com ele: o foco vai ao conteúdo ANTES (como na gaveta do menu), em
  // vez de cair no `<body>` junto com o painel.
  const seguir = () => {
    fechar();
    focarConteudo();
  };
  const link = (href: string, texto: string) => <a className={styles.link} href={href} onClick={seguir}>{texto}</a>;

  return (
    <div className={styles.painel}>
      <p className={styles.resumo}>
        {nivel === 'ok'
          ? 'Nenhum motivo de atenção: servidores no ar, aparelhos com estado conhecido e vagas dentro da capacidade.'
          : nivel === 'critico'
            ? 'Há um problema crítico. Resolva-o antes de confiar nos números da tela.'
            : 'O ambiente funciona, mas algo pede atenção.'}
      </p>
      {lista.length > 0 ? (
        <ul className={styles.lista} aria-label="Motivos">
          {lista.map((m) => (
            <li key={m.chave} className={styles.motivo} data-nivel={m.nivel}>
              <span className={styles.motivoNivel}>{m.nivel === 'critico' ? 'Crítico' : 'Atenção'}</span>
              <p className={styles.motivoTexto}>{m.texto}</p>
              {m.dica ? <p className={styles.motivoDica}>{m.dica}</p> : null}
              {m.destino ? link(m.destino, m.rotuloDestino ?? 'Abrir') : null}
            </li>
          ))}
        </ul>
      ) : null}
      {/* Não mudam a cor do semáforo: são estado de trabalho, não de saúde. Mas o número vem com o nome do que conta
          e leva à lista — o "15 bloqueadas" de antes não dizia se eram personas ou tarefas. */}
      {(bloqueadas ?? 0) > 0 || aguardando > 0 ? (
        <>
          <p className={styles.subtitulo}>Também</p>
          <ul className={styles.avisos}>
            {bloqueadas ? (
              <li>{link(hashDe('personas', { query: { situacao: 'bloqueada' } }),
                        `${bloqueadas} ${bloqueadas === 1 ? 'persona bloqueada' : 'personas bloqueadas'} pela plataforma`)}</li>
            ) : null}
            {aguardando > 0 ? (
              <li>{link(hashDe('pendencias'),
                        `${aguardando} ${aguardando === 1 ? 'pendência esperando' : 'pendências esperando'} você`)}</li>
            ) : null}
          </ul>
        </>
      ) : null}
      <div className={styles.rodape}>
        <span>{versao ? `Servidor central v${versao}` : ''}</span>
        <span className={styles.rodapeLinks}>
          {link(hashDe('infraestrutura'), 'Infraestrutura')}
          {link(hashDe('diagnostico'), 'Diagnóstico')}
        </span>
      </div>
    </div>
  );
}
