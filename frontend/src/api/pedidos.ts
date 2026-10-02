/**
 * Tipos do adendo v0.45 do contrato (Fase 28, item 28.9: pedidos persistentes), copiados do bloco "Tipos (TypeScript)"
 * de `docs/api-contract.md`. Ficam num arquivo próprio e não em `types.ts` (cuja parte 1 é cópia literal do contrato
 * base): o pedido é um contexto novo, com rotas próprias (`features/pedidos/api.ts`).
 *
 * Nomes de campo = colunas da migração 067; o que é CALCULADO pela API está marcado no contrato e aqui também.
 */
import type { ResolvedTarget, RunStatus, TargetQuestion } from './types';

export type Autonomia = 'observar' | 'preparar' | 'agir';
export type Sobreposicao = 'pular' | 'guardar_uma' | 'permitir_todas';
export type TipoDeGatilho = 'agora' | 'horario' | 'recorrencia' | 'evento' | 'condicao' | 'persona';
export type OrigemOcorrencia = 'agenda' | 'recuperacao' | 'evento' | 'condicao' | 'persona' | 'manual' | 'backfill';
export type MotivoDeEncerramento = 'prazo' | 'contagem' | 'orcamento' | 'abandonado';
export type EstadoPedido = 'rascunho' | 'ativo' | 'pausado' | 'aguardando_pessoa' | 'concluido' | 'encerrado' | 'cancelado';
export type EstadoOcorrencia = 'prevista' | 'devida' | 'despachada' | 'rodando' | 'concluida' | 'falhou' | 'incerta'
  | 'cancelada' | 'pulada' | 'perdida';
export type AcaoDePedido = 'editar' | 'ativar' | 'pausar' | 'retomar' | 'cancelar' | 'executar' | 'backfill';

/** O `targets` de `POST /api/runs/targets/resolve`, fotografado no pedido. */
export interface PedidoAlvos {
  targets: { instance_id: string | null; profile_id: string; app_id: string | null;
             origem: 'ui' | 'texto' | 'vinculo' | 'balanceamento' }[];
  device_policy: 'one' | 'primary' | 'all';
}

export interface PedidoDTO {
  id: string;
  titulo: string;
  objetivo: string;
  contexto: string | null;
  criterios_sucesso: string[] | null;
  alvos: PedidoAlvos | null;
  autonomia: Autonomia;
  fuso: string;
  inicio_em: string | null;
  fim_em: string | null;
  max_ocorrencias: number | null;
  orcamento_total_usd: number | null;
  orcamento_ocorrencia_usd: number | null;
  sobreposicao: Sobreposicao;
  janela_recuperacao_s: number | null;
  coalescer: boolean;
  max_tentativas: number;
  pausa_por_falha: number;
  estado: EstadoPedido;
  versao: number;
  proxima_em: string | null;
  criado_por: string | null;
  pausado_motivo: string | null;
  encerrado_motivo: MotivoDeEncerramento | null;
  pai_id: string | null;
  criado_em: string;
  atualizado_em: string;
}

export type GatilhoSpec =
  | Record<string, never>                                    // agora
  | { dtstart: string }                                      // horario: hora LOCAL ingênua 'YYYY-MM-DDTHH:MM:SS'
  | { dtstart: string; rrule: string }                       // recorrencia: subconjunto da RFC 5545
  | Record<string, unknown>;                                 // evento, condicao, persona: forma do 28.8

export interface GatilhoDTO {
  id: string;
  tipo: TipoDeGatilho;
  ativo: boolean;
  criado_em: string;
  spec: GatilhoSpec;
  cursor: string | null;
}

export interface OcorrenciaDTO {
  id: string;
  pedido_id: string;
  pedido_versao: number;
  gatilho_id: string | null;
  previsto_para: string;                    // instante canônico (segundo cheio, 'Z')
  chave: string;
  origem: OrigemOcorrencia;
  estado: EstadoOcorrencia;
  tentativa: number;
  run_id: string | null;
  run: { id: string; short_id: string; status: RunStatus; status_detail: string | null } | null;   // CALCULADO
  run_disponivel: boolean;                  // CALCULADO: false com run_id preenchido = a execução foi purgada
  motivo: string | null;
  custo_usd: number;
  resumo: string | null;
  criada_em: string;
  iniciada_em: string | null;
  terminada_em: string | null;
}

export interface PedidoView extends PedidoDTO {
  gatilhos_resumo: { tipo: TipoDeGatilho; descricao: string }[];
  personas: { profile_id: string; nome: string }[];
  proxima_local: string | null;
  ultima_ocorrencia: { id: string; estado: EstadoOcorrencia; terminada_em: string | null; motivo: string | null;
                       run_id: string | null } | null;
  ocorrencias_por_estado: Partial<Record<EstadoOcorrencia, number>>;
  gasto_usd: number;
  orcamento_usado: number | null;
  avisos_nao_lidos: number;
  acoes_permitidas: AcaoDePedido[];
}

export interface ProximaData {
  gatilho: number;
  nominal: string;
  local: string;
  utc: string;
  desviado: boolean;                        // a hora local não existia (salto do horário de verão)
  repetido: boolean;                        // a hora local aconteceu duas vezes: roda só na primeira
}

export interface PendenciaDoPedido {
  tipo: 'aprovacao' | 'pergunta' | 'ocorrencia_incerta';
  ref: string;
  run_id: string | null;
  ocorrencia_id: string | null;
  desde: string;
}

export interface PedidoDetalhe extends PedidoView {
  gatilhos: GatilhoDTO[];
  proximas: ProximaData[];
  ocorrencias_recentes: OcorrenciaDTO[];
  execucoes_em_curso: { run_id: string; ocorrencia_id: string; status: RunStatus }[];
  pendencias: PendenciaDoPedido[];
  /** `null` = ainda não existe (28.7); `[]` = existe e está vazio. */
  memoria: unknown | null;
  relatorios_recentes: unknown[] | null;
  observacoes_recentes: unknown[] | null;
}

// ---------------------------------------------------------------- criação, prévia e edição

export interface PedidoCorpo {
  objetivo: string;
  contexto?: string;
  criterios_sucesso?: string[];
  alvos: { instance_ids?: string[]; profile_ids?: string[];
           targets?: { profile_id: string; instance_ids?: string[]; app_id?: string | null }[];
           device_policy?: 'one' | 'primary' | 'all' };   // sem `distribute`: o backend recusa (422)
  autonomia?: Autonomia;
  fuso?: string;
  gatilhos: { tipo: TipoDeGatilho; spec: GatilhoSpec }[];
  inicio_em?: string;
  fim_em?: string;
  max_ocorrencias?: number;
  orcamento_total_usd?: number;
  orcamento_ocorrencia_usd?: number;
  sobreposicao?: Sobreposicao;
  janela_recuperacao_s?: number;
  coalescer?: boolean;
  max_tentativas?: number;
  pausa_por_falha?: number;
}

export interface CustoDoPedido {
  base: 'mediana_das_ultimas_5' | 'teto_por_ocorrencia' | 'sem_base';
  por_ocorrencia_usd: number | null;
  ocorrencias_por_mes: number | null;
  por_mes_usd: number | null;
}

export interface PedidoPrevia {
  valido: boolean;
  objetivo_sem_destinos: string;
  alvos: { targets: ResolvedTarget[]; questions: TargetQuestion[]; command_sem_destinos: string | null; warnings: string[] };
  proximas: ProximaData[];
  intervalo_minimo_s: number | null;
  autonomia: { teto: Autonomia; exige_aprovacao: string[]; recusado: string[] };
  custo: CustoDoPedido;
  bloqueios: { codigo: string; campo?: string; mensagem: string }[];
  alertas: { codigo: string; mensagem: string }[];
  confirmacao: string | null;
}

export interface PedidoCriacao extends PedidoCorpo {
  idempotency_key: string;
  titulo?: string;
  confirmacao?: string;
}

/** `201 PedidoView`; `200 {…, deduplicated: true}` quando a chave já existia. */
export type PedidoCriado = PedidoView & { deduplicated?: boolean };

export interface PedidoEdicao extends Partial<PedidoCorpo> {
  versao: number;
  titulo?: string;
  dry_run?: boolean;
  confirmacao?: string;
}

export interface PedidoEdicaoResultado {
  aplicado: boolean;
  pedido: PedidoView;
  mudancas: { campo: string; de: unknown; para: unknown }[];
  proximas_antes: ProximaData[];
  proximas_depois: ProximaData[];
  ocorrencias_refeitas: number;
  custo: CustoDoPedido;
  confirmacao: string | null;
}

// ---------------------------------------------------------------- leitura

export interface FiltroDePedidos {
  estado?: string;                  // um ou mais dos sete, separados por vírgula
  autonomia?: Autonomia;
  tipo?: TipoDeGatilho;
  profile_id?: string;
  q?: string;
  pede_atencao?: '1';
  ordem?: 'atualizado' | 'proxima' | 'criado';
  limit?: number;
  cursor?: string;
}

export interface ListaDePedidos {
  items: PedidoView[];
  proximo_cursor: string | null;
  total_por_estado: Partial<Record<EstadoPedido, number>>;
}

export interface ListaDeOcorrencias {
  items: OcorrenciaDTO[];
  proximo: string | null;
}

// ---------------------------------------------------------------- ações

export interface PedidoSemMudanca { pedido: PedidoView; sem_mudanca: boolean }
export interface PedidoRetomado extends PedidoSemMudanca { puladas: number; recuperadas: number }
export interface PedidoCancelado extends PedidoSemMudanca {
  execucoes_em_curso: { run_id: string; entregue?: boolean }[];
  ocorrencias_canceladas: number;
}

// ---------------------------------------------------------------- avisos

export type AvisoTipo = 'pausa_automatica' | 'orcamento_80' | 'orcamento_esgotado' | 'ocorrencia_perdida'
  | 'relatorio_pronto' | 'encerramento'                                  // informativos: caixa de avisos
  | 'aprovacao_pendente' | 'pergunta' | 'ocorrencia_incerta';           // requer_pessoa: caixa de Pendências

export interface AvisoDTO {
  id: string;
  pedido_id: string;
  pedido_titulo: string;
  ocorrencia_id: string | null;
  tipo: AvisoTipo;
  nivel: 'info' | 'warn' | 'error';
  mensagem: string;
  dados: Record<string, unknown>;
  requer_pessoa: boolean;
  criado_em: string;
  lido_em: string | null;
}

export interface ListaDeAvisos {
  items: AvisoDTO[];
  nao_lidos: number;
  proximo_cursor: string | null;
}
