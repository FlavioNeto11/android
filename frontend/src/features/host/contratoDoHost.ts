/**
 * 31.180: o contrato do painel do host, o leitor e os dados de exemplo, num arquivo só: quando a rota real chegar (a Jev, depois do
 * 31.173), troca-se `EXEMPLO_ATE_A_ROTA` e fica tudo.
 *
 * PROPOSTA de contrato (do Portal; ainda não é adendo): `GET /api/host/amostras?horas=1..24` → `{items: [AmostraDoHost]}`, da mais
 * antiga à mais nova, uma por minuto, com as colunas do CSV do `scripts/amostrador-host.ps1` (29.156). Sem nome de máquina, sem
 * linha de comando de processo, sem usuário. A linha de falha do amostrador vem como `{ts_utc, erro}`.
 *
 *   ts_utc                 AAAA-MM-DDTHH:MM:SSZ
 *   cpu_host_pct           CPU total do host (%)
 *   vm_convidado_nucleos   núcleos que as VMs usam (a do WSL é a do Docker); null = contador ausente
 *   vmmem_ws_mb            memória residente da VM do WSL (MB); null = ausente
 *   qemu_host_pct          CPU dos emuladores em % do host; null na 1ª linha de cada execução
 *   ram_livre_mb           RAM física livre (MB)
 *   disco_livre_gb         disco livre onde está o checkout (GB)
 *   processos_top          até 3 {nome, pct} com mais CPU no minuto (a lista, ou o texto do CSV "python:12.3;pwsh:4.1")
 *   avisos_pressao         {instance_id, n} dos avisos "Convidado sob pressão de CPU" do minuto (ou o texto "android-05:3;android-01:1");
 *                          vazio = nenhum OU não medido (banco indisponível)
 *
 *   cpu_media_pct          (v2 do amostrador, 29.185) CPU total do host como MÉDIA do minuto; `cpu_host_pct` é só o instantâneo de uma janela
 *                          curta e oscila de 8 % a 91 % entre minutos vizinhos; null na 1ª linha de cada execução
 *   demais_processos_pct   (v2) CPU dos processos vivos nas duas pontas do minuto que NÃO estão em `processos_top` nem no qemu
 *   nao_atribuido_pct      (v2) `cpu_media_pct` menos todos os processos: o que nasce e morre dentro do minuto, núcleo/interrupções e o
 *                          tempo da VM. Pode dar uns décimos NEGATIVOS por arredondamento; null na 1ª linha de cada execução
 *
 * As três colunas só crescem no fim do CSV: o amostrador anterior não as manda, e o leitor trata a ausência como "não medido".
 * O leitor aceita as duas formas (lista ou texto do CSV) e nunca troca "não medido" por zero.
 */
import { ApiError, apiRequest, toApiError } from '../../api/client';

export interface ProcessoDoHost { nome: string; pct: number }
export interface AvisoDePressao { instance_id: string; n: number }

export interface AmostraDoHost {
  ts_utc: string;
  cpu_host_pct: number | null;
  vm_convidado_nucleos: number | null;
  vmmem_ws_mb: number | null;
  qemu_host_pct: number | null;
  ram_livre_mb: number | null;
  disco_livre_gb: number | null;
  processos_top: ProcessoDoHost[];
  avisos_pressao: AvisoDePressao[];
  /** v2 do amostrador (29.185); `null` = não medido (1ª linha da execução, ou amostrador anterior). */
  cpu_media_pct: number | null;
  demais_processos_pct: number | null;
  nao_atribuido_pct: number | null;
}

export interface AmostrasDoHost {
  /** Da mais antiga à mais nova; a linha de falha do amostrador não entra aqui, só na contagem. */
  amostras: AmostraDoHost[];
  falhas: number;
  /** Os dados vêm de exemplo (a rota ainda não existe no central), não do host. */
  exemplo: boolean;
}

const registro = (v: unknown): Record<string, unknown> | null => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
/** Número finito (também o que vem como texto do CSV); o resto é "não medido". */
const numero = (v: unknown): number | null => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v.replace(',', '.')) : NaN;
  return Number.isFinite(n) ? n : null;
};

/** `python:12.3;pwsh:4.1` ou `[{nome, pct}]`; par sem número ou sem nome cai. */
function lerPares<T>(v: unknown, nomeDoCampo: string, valorDoCampo: string, montar: (nome: string, valor: number) => T): T[] {
  if (Array.isArray(v)) {
    return v.flatMap((x) => {
      const o = registro(x);
      const nome = o && typeof o[nomeDoCampo] === 'string' ? (o[nomeDoCampo] as string).trim() : '';
      const valor = o ? numero(o[valorDoCampo]) : null;
      return nome && valor !== null ? [montar(nome, valor)] : [];
    });
  }
  if (typeof v === 'string') {
    return v.split(';').flatMap((par) => {
      const i = par.lastIndexOf(':');
      const nome = par.slice(0, i).trim();
      const valor = i > 0 ? numero(par.slice(i + 1)) : null;
      return nome && valor !== null ? [montar(nome, valor)] : [];
    });
  }
  return [];
}

export function lerAmostra(v: unknown): AmostraDoHost | null {
  const o = registro(v);
  const ts = o && typeof o.ts_utc === 'string' && Number.isFinite(Date.parse(o.ts_utc)) ? o.ts_utc : null;
  if (!o || !ts || o.erro !== undefined) return null;
  return {
    ts_utc: ts, cpu_host_pct: numero(o.cpu_host_pct), vm_convidado_nucleos: numero(o.vm_convidado_nucleos), vmmem_ws_mb: numero(o.vmmem_ws_mb),
    qemu_host_pct: numero(o.qemu_host_pct), ram_livre_mb: numero(o.ram_livre_mb), disco_livre_gb: numero(o.disco_livre_gb),
    processos_top: lerPares(o.processos_top, 'nome', 'pct', (nome, pct) => ({ nome, pct })),
    avisos_pressao: lerPares(o.avisos_pressao, 'instance_id', 'n', (instance_id, n) => ({ instance_id, n })),
    cpu_media_pct: numero(o.cpu_media_pct), demais_processos_pct: numero(o.demais_processos_pct), nao_atribuido_pct: numero(o.nao_atribuido_pct),
  };
}

/** `null` quando a resposta não tem `items` (resposta inválida não é "host sem amostras"). */
export function lerAmostras(v: unknown, exemplo = false): AmostrasDoHost | null {
  const itens = registro(v)?.items;
  if (!Array.isArray(itens)) return null;
  const amostras = itens.map(lerAmostra).filter((a): a is AmostraDoHost => a !== null).sort((a, b) => Date.parse(a.ts_utc) - Date.parse(b.ts_utc));
  return { amostras, falhas: itens.filter((x) => registro(x)?.erro !== undefined).length, exemplo };
}

// ---- dados de exemplo (até a rota existir) ----------------------------------------------------------------------------------------------

/** Pseudo-aleatório determinístico: o exemplo é o mesmo a cada leitura da mesma hora, e o teste pode afirmar números. */
const ruido = (i: number, sal: number): number => { const x = Math.sin(i * 12.9898 + sal * 78.233) * 43758.5453; return x - Math.floor(x); };

/** Uma amostra por minuto, terminando em `agora`, com um pico de CPU e avisos de pressão num aparelho. Inventado: não veio do host. */
export function amostrasDeExemplo(horas: number, agora: number = Date.now()): AmostrasDoHost {
  const n = Math.max(1, Math.min(24, Math.trunc(horas))) * 60;
  const amostras: AmostraDoHost[] = Array.from({ length: n }, (_, k) => {
    const i = k - n + 1;                                                    // 0 = agora; negativo = antes
    const pico = Math.abs(i + 22) < 6;
    const cpu = Math.min(99, 24 + 14 * ruido(k, 1) + (pico ? 48 : 0));
    const qemu = Math.round((cpu * 0.55) * 10) / 10;
    const topo = [Math.round((6 + ruido(k, 5) * 6 + (pico ? 20 : 0)) * 10) / 10, Math.round((2 + ruido(k, 6) * 3) * 10) / 10, 1.1];
    const demais = Math.round((1 + ruido(k, 7) * 2) * 10) / 10;
    const naoAtribuido = Math.round((1.5 + ruido(k, 8) * 5) * 10) / 10;
    return {
      ts_utc: new Date(Math.floor(agora / 60_000) * 60_000 + i * 60_000).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      cpu_host_pct: Math.round(cpu * 10) / 10,
      vm_convidado_nucleos: Math.round((1.6 + ruido(k, 2) * 0.8 + (pico ? 1.2 : 0)) * 100) / 100,
      vmmem_ws_mb: 2900 + Math.round(ruido(k, 3) * 120),
      qemu_host_pct: k === 0 ? null : qemu,
      ram_livre_mb: 5200 - Math.round(ruido(k, 4) * 400) - (pico ? 900 : 0),
      disco_livre_gb: 212.4,
      processos_top: [{ nome: 'python', pct: topo[0]! }, { nome: 'node', pct: topo[1]! }, { nome: 'pwsh', pct: topo[2]! }],
      avisos_pressao: pico ? [{ instance_id: 'android-05', n: 3 }, { instance_id: 'android-01', n: 1 }] : [],
      // A média do minuto é a soma das partes (o exemplo é coerente de propósito); a 1ª linha não tem referência anterior.
      cpu_media_pct: k === 0 ? null : Math.round((qemu + topo[0]! + topo[1]! + topo[2]! + demais + naoAtribuido) * 10) / 10,
      demais_processos_pct: k === 0 ? null : demais,
      nao_atribuido_pct: k === 0 ? null : naoAtribuido,
    };
  });
  return { amostras, falhas: 0, exemplo: true };
}

// ---- a leitura -------------------------------------------------------------------------------------------------------------------------

/** 404 de ROTA que não existe (o central anterior ao módulo). */
const rotaAusente = (e: unknown): boolean => toApiError(e).status === 404;

export const JANELAS_EM_HORAS = [1, 6, 24] as const;
export type JanelaEmHoras = (typeof JANELAS_EM_HORAS)[number];

export const apiHost = {
  async amostras(horas: number, signal?: AbortSignal): Promise<AmostrasDoHost> {
    try {
      const lido = lerAmostras(await apiRequest<unknown>('GET', '/host/amostras', { query: { horas: String(horas) }, signal }));
      if (!lido) throw new ApiError(502, 'resposta_invalida', 'A resposta das amostras do host veio em formato inesperado.');
      return lido;
    } catch (e) {
      if (rotaAusente(e)) return amostrasDeExemplo(horas);
      throw e;
    }
  },
};
