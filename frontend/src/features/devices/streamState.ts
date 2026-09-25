import type { Instance } from '../../api/types';

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
