import type { Instance } from '../../api/types';
import { parseTs } from '../../lib/time';

/**
 * O que dizer quando a tela não está ao vivo. "Desatualizado" sozinho cobria cinco situações diferentes; o
 * backend agora nomeia cada uma (`instance.stream`, ver `backend/app/devices/stream.py`) e aqui só se traduz.
 *
 * `clientStale` continua valendo: um backend que parou de mandar eventos não tem como avisar, então a idade do
 * frame conferida no cliente decide SE há selo. O `stream` decide QUAL selo. Como `stream` só chega nos eventos
 * de instância (não em todo frame), `live`/`stale` guardados podem ser velhos: por isso só `capture_error` e
 * `worker_offline` — que são publicados quando mudam — ganham selo próprio; o resto é "sem frame novo".
 */
export interface StreamLabel {
  title: string;
  hint: string;
  tone: 'warning' | 'danger';
}

/**
 * Prévia suspensa (contrato C3): o aparelho está online e o backend parou de capturar porque ninguém olhava. Não é
 * "desatualizado" nem falha — o frame antigo continua valendo como foto daquele instante, com a hora dele.
 *
 * `stream` só é atualizado por `instance.updated`; os frames chegam por `frame`. Quando o interesse volta, o
 * backend captura na hora e o frame novo pode chegar ANTES do evento que tira o `paused`: frame mais novo que o
 * último que o `stream` conhecia prova que a prévia voltou, e o selo sai sem esperar.
 */
export function isPreviewPaused(inst: Pick<Instance, 'state' | 'frame' | 'stream'>): boolean {
  if (inst.state !== 'online' || inst.stream?.status !== 'paused') return false;
  const frameTs = parseTs(inst.frame?.ts);
  const knownTs = parseTs(inst.stream.last_frame_at);
  if (frameTs === null) return true;
  // Pausou sem frame e agora há um, ou chegou frame mais novo que o da pausa: a captura voltou.
  return knownTs !== null && frameTs <= knownTs;
}

export const PAUSED_LABEL = {
  title: 'Prévia suspensa',
  hint: 'Ninguém estava olhando este aparelho, então a captura da prévia parou para poupar o servidor. O aparelho '
    + 'segue online e trabalhando; a imagem volta sozinha assim que ele aparecer na tela.',
} as const;

/**
 * Tela sensível (contrato C4): o último frame é um MARCADOR sem imagem — campo de senha, código de verificação,
 * tela declarada sensível ou a VM-loja. A imagem não sai do aparelho; não é falha nem atraso.
 */
export const SENSITIVE_LABEL = {
  title: 'Tela sensível — prévia oculta',
  hint: 'A tela tem campo de senha, código de verificação ou é da loja: a imagem não sai do aparelho. O controle '
    + 'manual continua valendo, às cegas, com o tamanho da tela.',
} as const;

/** Falhas que continuam valendo mesmo com a tela sensível: são publicadas quando mudam, e dizem que a TELA parou. */
export function isScreenFailure(inst: Pick<Instance, 'stream'>): boolean {
  return inst.stream?.status === 'capture_error' || inst.stream?.status === 'worker_offline';
}

export function streamLabel(inst: Pick<Instance, 'state' | 'stream'>, clientStale: boolean): StreamLabel | null {
  if (!clientStale) return null;
  const st = inst.stream;
  if (st?.status === 'worker_offline') {
    return { title: 'Servidor desconectado', tone: 'danger',
             hint: 'O worker que hospeda este aparelho caiu: o estado real lá é desconhecido.' };
  }
  if (st?.status === 'capture_error') {
    const n = st.consecutive_capture_failures;
    return { title: 'Captura falhando', tone: 'danger',
             hint: `A captura de tela falhou ${n}x seguidas${st.last_capture_error ? ` (${st.last_capture_error})` : ''}. `
               + 'O aparelho segue online.' };
  }
  if (st?.status === 'device_offline' || st?.status === 'device_hibernated') {
    return { title: 'Frame histórico', tone: 'warning', hint: st.detail };
  }
  return { title: 'Sem frame novo', tone: 'warning',
           hint: 'O aparelho segue online; a captura está atrasada ou o canal de eventos parou. Isto não é '
             + '“aparelho offline”.' };
}
