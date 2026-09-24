/**
 * Item 11.6 — Interações como linha do tempo vertical, agrupada por dia, com ícone e cor por tipo. Usada
 * em tamanho reduzido (8 mais recentes) na Visão geral e por inteiro na aba Interações, com filtro por tipo.
 */
import {
  Heart, MessageCircle, MessageCircleReply, MessagesSquare, type LucideIcon, UserMinus, UserPlus,
} from 'lucide-react';
import type { SocialInteraction } from '../../api/types';
import { Badge } from '../../components/Badge';
import { cx } from '../../lib/format';
import type { Tone } from '../../lib/status';
import styles from './Profiles.module.css';

interface TipoMeta { label: string; icon: LucideIcon; tone: Tone }

const TIPO_META: Record<string, TipoMeta> = {
  dm_sent: { label: 'Mensagem enviada', icon: MessageCircle, tone: 'accent' },
  dm_received: { label: 'Mensagem recebida', icon: MessageCircleReply, tone: 'info' },
  comment_replied: { label: 'Comentário respondido', icon: MessagesSquare, tone: 'accent' },
  comment_posted: { label: 'Comentário publicado', icon: MessagesSquare, tone: 'accent' },
  post_liked: { label: 'Curtida', icon: Heart, tone: 'success' },
  followed: { label: 'Seguiu', icon: UserPlus, tone: 'success' },
  unfollowed: { label: 'Deixou de seguir', icon: UserMinus, tone: 'muted' },
};

export function tipoMeta(tipo: string): TipoMeta {
  return TIPO_META[tipo] ?? { label: tipo, icon: MessagesSquare, tone: 'neutral' };
}

/** "Hoje", "Ontem" ou a data por extenso — o mesmo texto usado para agrupar a linha do tempo. */
export function rotuloDoDia(iso: string, agora: Date = new Date()): string {
  const d = new Date(iso);
  const mesmoDia = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (mesmoDia(d, agora)) return 'Hoje';
  const ontem = new Date(agora);
  ontem.setDate(agora.getDate() - 1);
  if (mesmoDia(d, ontem)) return 'Ontem';
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
}

/** Agrupa preservando a ordem de chegada — a lista já vem do mais recente ao mais antigo. */
export function agruparPorDia<T>(itens: T[], dataDe: (item: T) => string, agora: Date = new Date()): { rotulo: string; itens: T[] }[] {
  const grupos: { rotulo: string; itens: T[] }[] = [];
  for (const item of itens) {
    const rotulo = rotuloDoDia(dataDe(item), agora);
    const ultimo = grupos.at(-1);
    if (ultimo && ultimo.rotulo === rotulo) ultimo.itens.push(item);
    else grupos.push({ rotulo, itens: [item] });
  }
  return grupos;
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'Pendente', confirmed: 'Confirmada', failed: 'Falhou', uncertain: 'Incerta', cancelled: 'Cancelada',
};

const STATUS_TONE: Record<string, Tone> = {
  confirmed: 'success', pending: 'warning', failed: 'danger', uncertain: 'warning', cancelled: 'muted',
};

function ItemLinha({ item }: { item: SocialInteraction }) {
  const meta = tipoMeta(item.type);
  const Icon = meta.icon;
  const entrando = item.direction === 'in' || item.direction === 'inbound' || item.direction === 'received';
  const trecho = item.incoming_content || item.outgoing_content;
  return (
    <li className={styles.timelineItem}>
      <span className={cx(styles.timelineIcon, styles[`toneIcon_${meta.tone}`])}>
        <Icon size={14} aria-hidden />
      </span>
      <div className={styles.timelineBody}>
        <div className={styles.timelineHead}>
          <strong>{meta.label}</strong>
          <span className={styles.muted}>
            {entrando ? 'recebido' : 'enviado'}{item.counterparty ? ` · ${item.counterparty}` : ''}
          </span>
          <Badge size="sm" tone={STATUS_TONE[item.status] ?? 'neutral'}>{STATUS_LABEL[item.status] ?? item.status}</Badge>
        </div>
        {trecho ? <p className={styles.bubble}>{trecho}</p> : null}
      </div>
    </li>
  );
}

/** Linha do tempo vertical agrupada por dia. `limite` corta para a mini versão da Visão geral. */
export function InteractionTimeline({ itens, limite }: { itens: SocialInteraction[]; limite?: number }) {
  const lista = limite ? itens.slice(0, limite) : itens;
  const grupos = agruparPorDia(lista, (i) => i.occurred_at);
  return (
    <div className={styles.timeline}>
      {grupos.map((g) => (
        <div key={g.rotulo} className={styles.timelineGroup}>
          <h4 className={styles.timelineDay}>{g.rotulo}</h4>
          <ul className={styles.timelineList}>
            {g.itens.map((i) => <ItemLinha key={i.id} item={i} />)}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** Etiquetas de filtro por tipo, no topo da aba Interações. */
export function TimelineFilter({ tipos, ativo, onChange }: {
  tipos: string[]; ativo: string | null; onChange: (t: string | null) => void;
}) {
  return (
    <div className={styles.filterBar} role="group" aria-label="Filtrar por tipo de interação">
      <button type="button" className={cx(styles.filterTag, ativo === null && styles.filterTagActive)}
              onClick={() => onChange(null)}>Todas</button>
      {tipos.map((t) => (
        <button key={t} type="button" className={cx(styles.filterTag, ativo === t && styles.filterTagActive)}
                onClick={() => onChange(t)}>{tipoMeta(t).label}</button>
      ))}
    </div>
  );
}
