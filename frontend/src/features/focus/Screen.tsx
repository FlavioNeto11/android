import { Clock, Eye, Hand, ImageOff, LoaderCircle, Moon, Pause, PowerOff, RefreshCw, TriangleAlert } from 'lucide-react';
import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState, type PointerEvent as ReactPointerEvent,
} from 'react';
import { api, hintForError, toApiError } from '../../api/client';
import type { FrameInfo, Instance } from '../../api/types';
import { Button } from '../../components/Button';
import { containedRect, resolveFrameSize } from '../../lib/coords';
import { cx } from '../../lib/format';
import { LONG_PRESS_MS, isDrag, resolveGesture, type Gesture, type PointerSample } from '../../lib/gesture';
import { localId } from '../../lib/ids';
import { INSTANCE_STATE } from '../../lib/status';
import { tempoRelativo, useNow } from '../../lib/time';
import { useAppStore } from '../../store/app';
import { useFrameStale } from '../devices/DeviceCard';
import { SELO_DESCONHECIDO } from '../devices/selos';
import { PAUSED_LABEL, isPreviewPaused, streamLabel } from '../devices/streamState';
import styles from './Focus.module.css';

/** Frame REALMENTE exibido: id/tamanho vêm dos cabeçalhos X-Frame-* da resposta que gerou a imagem. */
export interface ShownFrame {
  url: string;
  id: string;
  ts: string | null;
  width: number;
  height: number;
}

export interface ScreenHandle {
  /** Força a busca de um frame novo (ex.: após 409 stale_frame). */
  refresh: () => void;
  /** Frame que o usuário está vendo agora — é o `frame_id` de toda entrada manual. */
  shown: () => ShownFrame | null;
}

interface ScreenProps {
  instance: Instance;
  /**
   * O servidor do aparelho está fora do ar ou sem canal (RF-40; quem decide é `devices/selos::aparelhoDesconhecido`).
   * O `state` guardado é velho: nem a tela ao vivo nem o "Parada" saem dele.
   */
  desconhecido?: boolean;
  /** O usuário tem o lease e pode interagir. */
  interactive: boolean;
  /** Uma entrada está em voo: novos gestos esperam. */
  busy: boolean;
  onGesture: (gesture: Gesture, frame: ShownFrame) => void;
  /** Retângulo (pixels do aparelho) a destacar — usado pela lista de hierarquia. */
  highlight: [number, number, number, number] | null;
  onShownChange?: (frame: ShownFrame | null) => void;
}

interface Ripple {
  key: string;
  x: number;
  y: number;
  long: boolean;
}

function FrameAge({ ts }: { ts: string | null }) {
  const now = useNow();
  return <>{ts ? tempoRelativo(ts, now) : '—'}</>;
}

export const Screen = forwardRef<ScreenHandle, ScreenProps>(function Screen(
  { instance, desconhecido = false, interactive, busy, onGesture, highlight, onShownChange },
  ref,
) {
  const id = instance.id;
  const online = instance.state === 'online' && !desconhecido;
  const latestId = instance.frame?.id ?? null;

  const [shown, setShown] = useState<ShownFrame | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; hint: string } | null>(null);
  const [noFrame, setNoFrame] = useState(false);
  const [loading, setLoading] = useState(false);

  const shownRef = useRef<ShownFrame | null>(null);
  const staleUrls = useRef<string[]>([]);
  // FrameInfo dos últimos frames anunciados (os eventos `frame` chegam antes de o download terminar).
  const knownFrames = useRef(new Map<string, FrameInfo>());
  const rememberFrame = useCallback((f: FrameInfo) => {
    const map = knownFrames.current;
    map.delete(f.id);
    map.set(f.id, f);
    while (map.size > 32) {
      const oldest = map.keys().next().value;
      if (oldest === undefined) break;
      map.delete(oldest);
    }
  }, []);

  useEffect(() => {
    if (instance.frame) rememberFrame(instance.frame);
  }, [instance.frame, rememberFrame]);
  const inFlight = useRef(false);
  const again = useRef(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (inFlight.current) {
      again.current = true; // chegou frame novo durante o download: busca de novo ao terminar
      return;
    }
    inFlight.current = true;
    setLoading(true);
    try {
      const { blob, headers } = await api.fetchFrame(id, 'full');
      if (!alive.current) return;
      // X-Frame-Id diz QUAL frame está na imagem. O tamanho do aparelho vem do FrameInfo desse mesmo frame
      // (pixels do aparelho, garantido pelo contrato); os cabeçalhos de tamanho são só plano B.
      const latest = useAppStore.getState().instances[id]?.frame ?? null;
      if (latest) rememberFrame(latest);
      const frameId = headers.id ?? latest?.id ?? null;
      const match = frameId ? knownFrames.current.get(frameId) ?? null : null;
      const size = resolveFrameSize(match, { width: headers.width ?? undefined, height: headers.height ?? undefined }, latest);
      if (!frameId || !size) {
        setLoadError({ message: 'O servidor não informou o id e o tamanho da imagem (X-Frame-Id / FrameInfo).', hint: 'Sem isso não é seguro converter cliques em coordenadas do aparelho.' });
        return;
      }
      const next: ShownFrame = { url: URL.createObjectURL(blob), id: frameId, ts: headers.ts ?? match?.ts ?? latest?.ts ?? null, width: size.width, height: size.height };
      if (shownRef.current) staleUrls.current.push(shownRef.current.url);
      shownRef.current = next;
      setShown(next);
      setLoadError(null);
      setNoFrame(false);
    } catch (e) {
      if (!alive.current) return;
      const err = toApiError(e);
      if (err.status === 404) {
        setNoFrame(true);
        setLoadError(null);
      } else {
        setLoadError({ message: err.message, hint: hintForError(err) });
      }
    } finally {
      inFlight.current = false;
      if (alive.current) {
        setLoading(false);
        if (again.current) {
          again.current = false;
          void load();
        }
      }
    }
  }, [id, rememberFrame]);

  // Busca quando o painel abre, quando a instância fica online e sempre que o id do frame mudar.
  useEffect(() => {
    if (!online) return;
    // 31.350 (C): o servidor ainda não tem frame deste aparelho (`instance.frame` nulo): `GET /frame` daria 404, que o navegador
    // registra como erro vermelho no console mesmo tratado. Nada a buscar: a tela diz "Ainda não há imagem" e a busca sai
    // quando o primeiro frame chega (o `latestId` muda) ou quando a pessoa pede ("Atualizar imagem").
    if (!latestId) {
      if (!shownRef.current) setNoFrame(true);
      return;
    }
    if (shownRef.current?.id === latestId) return;
    void load();
  }, [online, latestId, load]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (shownRef.current) URL.revokeObjectURL(shownRef.current.url);
      for (const u of staleUrls.current) URL.revokeObjectURL(u);
      staleUrls.current = [];
      shownRef.current = null;
    };
  }, [id]);

  useEffect(() => {
    onShownChange?.(shown);
  }, [shown, onShownChange]);

  useImperativeHandle(ref, () => ({ refresh: () => void load(), shown: () => shownRef.current }), [load]);

  // ---- gestos ----
  const boxRef = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ pointerId: number; start: PointerSample; dragged: boolean } | null>(null);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ripples, setRipples] = useState<Ripple[]>([]);
  const [hold, setHold] = useState<{ x: number; y: number; armed: boolean } | null>(null);
  const [drag, setDrag] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const [boxSize, setBoxSize] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      const r = el.getBoundingClientRect();
      setBoxSize({ width: r.width, height: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [online]);

  const sample = (e: ReactPointerEvent): { s: PointerSample; box: { width: number; height: number } } | null => {
    const el = boxRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { s: { x: e.clientX - r.left, y: e.clientY - r.top, t: performance.now() }, box: { width: r.width, height: r.height } };
  };

  const clearHold = () => {
    if (holdTimer.current) clearTimeout(holdTimer.current);
    holdTimer.current = null;
    setHold(null);
  };

  const canGesture = interactive && !busy && !!shown;

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!canGesture || !shown || e.button !== 0) return;
    const cur = sample(e);
    if (!cur) return;
    // Cliques nas margens (fora da imagem contida) são ignorados.
    const rect = containedRect(cur.box, shown);
    if (!rect || cur.s.x < rect.left || cur.s.x > rect.left + rect.width || cur.s.y < rect.top || cur.s.y > rect.top + rect.height) return;
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* sem captura o gesto ainda funciona enquanto o ponteiro estiver sobre a tela */
    }
    gesture.current = { pointerId: e.pointerId, start: cur.s, dragged: false };
    setHold({ x: cur.s.x, y: cur.s.y, armed: false });
    holdTimer.current = setTimeout(() => setHold((h) => (h ? { ...h, armed: true } : h)), LONG_PRESS_MS);
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const g = gesture.current;
    if (!g || g.pointerId !== e.pointerId) return;
    const cur = sample(e);
    if (!cur) return;
    if (!g.dragged && isDrag(g.start, cur.s)) {
      g.dragged = true;
      clearHold();
    }
    if (g.dragged) setDrag({ x1: g.start.x, y1: g.start.y, x2: cur.s.x, y2: cur.s.y });
  };

  const finish = (e: ReactPointerEvent<HTMLDivElement>, cancelled: boolean) => {
    const g = gesture.current;
    if (!g || g.pointerId !== e.pointerId) return;
    gesture.current = null;
    clearHold();
    setDrag(null);
    if (cancelled || !shown) return;
    const cur = sample(e);
    if (!cur) return;
    const result = resolveGesture(g.start, cur.s, g.dragged, cur.box, shown);
    if (!result) return;
    const key = localId('ripple');
    const at = result.type === 'swipe' ? cur.s : g.start;
    setRipples((r) => [...r, { key, x: at.x, y: at.y, long: result.type === 'long_press' }]);
    setTimeout(() => setRipples((r) => r.filter((x) => x.key !== key)), 900);
    onGesture(result, shown);
  };

  useEffect(() => () => {
    if (holdTimer.current) clearTimeout(holdTimer.current);
  }, []);

  // ---- renderização ----
  const stale = useFrameStale(instance, true);
  const staleInfo = streamLabel(instance, stale);
  // Com o foco aberto o interesse existe; `paused` aqui é o instante até a captura voltar (ou a aba oculta).
  const paused = isPreviewPaused(instance);
  const hl = (() => {
    if (!highlight || !shown || !boxSize) return null;
    const rect = containedRect(boxSize, shown);
    if (!rect) return null;
    const sx = rect.width / shown.width;
    const sy = rect.height / shown.height;
    const [x1, y1, x2, y2] = highlight;
    return { left: rect.left + x1 * sx, top: rect.top + y1 * sy, width: Math.max(2, (x2 - x1) * sx), height: Math.max(2, (y2 - y1) * sy) };
  })();

  if (desconhecido) {
    // Nada do estado guardado aqui: nem o rótulo, nem o `state_detail` do backend ("Aparelho em 'stopped'").
    const Icon = SELO_DESCONHECIDO.icon;
    return (
      <div className={styles.screenFrame}>
        <div className={styles.screenState}>
          <Icon size={30} aria-hidden />
          <p className={styles.screenStateTitle}>{SELO_DESCONHECIDO.label}</p>
          <p>{SELO_DESCONHECIDO.description}</p>
          <p>A tela ao vivo e as ações voltam quando o servidor responder. Veja a seção “Servidor” ao lado.</p>
        </div>
      </div>
    );
  }

  if (!online) {
    const meta = INSTANCE_STATE[instance.state] ?? INSTANCE_STATE.error;
    const hibernated = instance.state === 'hibernated';
    const Icon = instance.state === 'booting' || instance.state === 'stopping' ? LoaderCircle : hibernated ? Moon : PowerOff;
    return (
      <div className={styles.screenFrame}>
        <div className={styles.screenState}>
          <Icon size={30} className={meta.spin ? 'spin' : undefined} aria-hidden />
          <p className={styles.screenStateTitle}>{meta.label}</p>
          <p>{instance.state_detail ?? meta.description}</p>
          <p>
            {hibernated
              ? 'Não há tela ao vivo enquanto o aparelho hiberna. Use “Acordar” ao lado: ele volta em segundos, do ponto em que parou.'
              : 'A tela ao vivo aparece quando a instância estiver online. Use as ações rápidas ao lado.'}
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div className={cx(styles.screenFrame, interactive && styles.screenFrameLive)}>
        <div
          ref={boxRef}
          className={cx(styles.screenBox, canGesture && styles.screenBoxInteractive, interactive && busy && styles.screenBoxBusy)}
          role="img"
          aria-label={`Tela ao vivo de ${id}${interactive ? '. Clique para tocar, segure para toque longo, arraste para deslizar.' : '. Somente visualização.'}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={(e) => finish(e, false)}
          onPointerCancel={(e) => finish(e, true)}
          onContextMenu={(e) => e.preventDefault()}
        >
          {shown ? (
            <img
              className={cx(styles.screenImg, stale && styles.screenImgStale)}
              src={shown.url}
              alt=""
              draggable={false}
              onLoad={() => {
                // A imagem nova já está na tela: as anteriores podem ser liberadas.
                for (const u of staleUrls.current) URL.revokeObjectURL(u);
                staleUrls.current = [];
              }}
            />
          ) : null}

          <div className={styles.screenOverlay} aria-hidden>
            {hl ? <span className={styles.highlight} style={hl} /> : null}
            {drag ? (
              <svg className={styles.dragSvg}>
                <line x1={drag.x1} y1={drag.y1} x2={drag.x2} y2={drag.y2} stroke="var(--accent-hover)" strokeWidth={3} strokeLinecap="round" strokeDasharray="2 7" />
                <circle cx={drag.x1} cy={drag.y1} r={7} fill="none" stroke="var(--accent-hover)" strokeWidth={2} />
                <circle cx={drag.x2} cy={drag.y2} r={9} fill="rgba(95,208,194,0.35)" stroke="var(--accent-hover)" strokeWidth={2} />
              </svg>
            ) : null}
            {hold ? <span className={cx(styles.hold, hold.armed && styles.holdArmed)} style={{ left: hold.x, top: hold.y }} /> : null}
            {ripples.map((r) => (
              <span key={r.key} className={cx(styles.ripple, r.long && styles.rippleLong)} style={{ left: r.x, top: r.y }} />
            ))}
          </div>
        </div>

        {!shown ? (
          <div className={styles.screenState}>
            {loadError ? (
              <>
                <ImageOff size={28} aria-hidden />
                <p className={styles.screenStateTitle}>Não foi possível carregar a tela</p>
                <p>{loadError.message}</p>
                <p>{loadError.hint}</p>
                <Button size="sm" variant="outline" icon={RefreshCw} onClick={() => void load()}>Tentar de novo</Button>
              </>
            ) : noFrame ? (
              <>
                <ImageOff size={28} aria-hidden />
                <p className={styles.screenStateTitle}>Ainda não há imagem deste aparelho</p>
                <p>A captura começa quando a automação fica pronta. A primeira imagem aparece aqui sozinha.</p>
                <Button size="sm" variant="outline" icon={RefreshCw} loading={loading} onClick={() => void load()}>Verificar agora</Button>
              </>
            ) : (
              <>
                <LoaderCircle size={28} className="spin" aria-hidden />
                <p className={styles.screenStateTitle}>Carregando a tela…</p>
              </>
            )}
          </div>
        ) : null}

        {/* `aria-live="off"`: o "há N s" muda a cada segundo e, como `role=status` é polite por padrão, o leitor
            de tela anunciava a etiqueta em loop (P3.3). A informação continua legível ao focar ou navegar. */}
        {shown && paused ? (
          <span className={styles.pausedTag} role="status" aria-live="off" title={PAUSED_LABEL.hint} data-stream="paused">
            <Pause size={13} aria-hidden /> {PAUSED_LABEL.title} — última imagem <FrameAge ts={instance.frame?.ts ?? shown.ts} />
          </span>
        ) : null}
        {shown && staleInfo ? (
          <span className={styles.staleTag} role="status" aria-live="off" title={staleInfo.hint} data-stream={instance.stream?.status ?? ''}>
            <TriangleAlert size={13} aria-hidden /> Desatualizado ({staleInfo.title}) — última imagem <FrameAge ts={instance.frame?.ts ?? shown.ts} />
          </span>
        ) : null}
        {shown ? (
          <span className={cx(styles.modeTag, interactive && styles.modeTagLive)}>
            {interactive ? <Hand size={13} aria-hidden /> : <Eye size={13} aria-hidden />}
            {interactive ? (busy ? 'Enviando a ação…' : 'Você está no controle — a tela responde aos seus cliques') : 'Somente visualização — assuma o controle para interagir'}
          </span>
        ) : null}
      </div>

      <div className={styles.screenMeta}>
        <span><Clock size={11} aria-hidden /> última imagem <FrameAge ts={shown?.ts ?? instance.frame?.ts ?? null} /></span>
        <span>{shown ? `${shown.width}×${shown.height} px` : '—'}</span>
        <Button size="sm" variant="ghost" icon={RefreshCw} loading={loading} onClick={() => void load()}>Atualizar imagem</Button>
      </div>
    </>
  );
});
