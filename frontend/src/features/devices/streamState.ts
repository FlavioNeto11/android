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
 * Com a IA no controle a prévia não captura por conta própria: a tela chega a cada observação da IA. Frame velho aí
 * é a IA sem olhar a tela há tempo demais — não a captura atrasada (r-20260928195344-02ee9e, android-06). Decide pelo
 * `control`, e não pelo `stream.status`: o cliente acha o frame velho pela idade, com o `live` guardado do último
 * `instance.updated`.
 */
export const AI_NOT_LOOKING_LABEL: StreamLabel = {
  title: 'IA sem olhar a tela', tone: 'warning',
  hint: 'A IA está operando o aparelho e a tela chega a cada observação dela; a última já passou do prazo — decisão, '
    + 'ação ou leitura demorada, ou aparelho sobrecarregado. Não é captura falhando nem aparelho offline.',
};

export function streamLabel(inst: Pick<Instance, 'state' | 'stream'> & Partial<Pick<Instance, 'control'>>,
                            clientStale: boolean): StreamLabel | null {
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
  if (inst.control === 'ai') return AI_NOT_LOOKING_LABEL;
  return { title: 'Sem imagem nova', tone: 'warning',
           hint: 'O aparelho segue online; a captura está atrasada ou o canal de eventos parou. Isto não é '
             + '“aparelho offline”.' };
}
