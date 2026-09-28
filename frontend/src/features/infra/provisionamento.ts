import type { ApiError } from '../../api/client';
import type { InstanceProvisionRequest } from '../../api/types';

/**
 * Criar e aposentar aparelho pela plataforma (adendo v0.26, ADR-045). Aqui fica só o que é regra: validar o pedido
 * antes de mandar e traduzir cada recusa do backend numa frase com o próximo passo. A tela mostra a frase E a
 * mensagem do backend — a frase diz o que fazer; a mensagem, o número exato que o servidor viu.
 */

/** Uma recusa como aparece na tela: título curto, o que fazer, e se vale levar a pessoa a Configuração → Limites. */
export interface RecusaNaTela {
  code: string;
  titulo: string;
  /** O próximo passo, em português, com os números do detalhe quando o backend os manda. */
  passo: string | null;
  /** A mensagem do backend, como veio. */
  mensagem: string;
  irParaLimites?: boolean;
}

const numero = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const gb = (v: number) => `${v.toLocaleString('pt-BR', { maximumFractionDigits: 1 })} GB`;

export function recusaDoProvisionamento(e: ApiError): RecusaNaTela {
  const d = e.detail ?? {};
  const base = { code: e.code, mensagem: e.message };
  switch (e.code) {
    case 'teto_de_aparelhos': {
      const tem = numero(d.devices);
      const teto = numero(d.max_devices);
      return {
        ...base, titulo: 'Teto de aparelhos atingido', irParaLimites: true,
        passo: `${tem !== null && teto !== null ? `Este servidor já tem ${tem} de ${teto} aparelhos. ` : ''}`
          + 'Suba o teto em Configuração → Limites → Por servidor, ou aposente um aparelho antes.',
      };
    }
    case 'disco_insuficiente': {
      const livre = numero(d.disk_free_gb);
      const piso = numero(d.min_free_disk_gb);
      return {
        ...base, titulo: 'Disco insuficiente',
        passo: `${livre !== null && piso !== null ? `Livre: ${gb(livre)}; mínimo para mais um aparelho: ${gb(piso)}. ` : ''}`
          + 'Libere espaço nesta máquina (ou aposente um aparelho) e tente de novo.',
      };
    }
    case 'disco_desconhecido':
      return { ...base, titulo: 'Disco ainda não medido',
               passo: 'Sem a medição do disco livre não se cria aparelho. Espere a próxima batida deste servidor e tente de novo.' };
    case 'provisionamento_remoto_indisponivel':
      return { ...base, titulo: 'Servidor remoto', passo: 'Crie o aparelho neste servidor, ou adote um que o worker anuncie.' };
    case 'conflito_de_provisionamento':
      return { ...base, titulo: 'Outro pedido em andamento', passo: 'Outro aparelho está sendo criado agora. Espere e tente de novo.' };
    case 'chave_ja_usada':
      return { ...base, titulo: 'Pedido repetido', passo: 'Este pedido já tinha sido usado para um aparelho que saiu do parque. Envie de novo: vai com uma chave nova.' };
    case 'servidor_nao_hospeda':
      return { ...base, titulo: 'Este backend não hospeda aparelhos', passo: 'Crie o aparelho pelo backend que hospeda os emuladores.' };
    case 'unknown_app':
      return { ...base, titulo: 'Aplicativo não cadastrado', passo: 'Escolha um aplicativo da lista (ou nenhum) e tente de novo.' };
    case 'sobreposicao_invalida':
      return { ...base, titulo: 'Imagem ou RAM inválida', passo: 'Revise a imagem do sistema e a RAM.' };
    default:
      return { ...base, titulo: 'Não foi possível criar o aparelho', passo: null };
  }
}

/** Recusas do `DELETE /api/instances/{id}`: cada uma diz o que falta fazer antes. */
const PASSO_DA_APOSENTADORIA: Record<string, string> = {
  aparelho_ligado: 'Desligue o aparelho antes de aposentá-lo.',
  vinculo_ativo: 'Desvincule a persona deste aparelho antes: a sessão dela vive no AVD que seria apagado.',
  objetivo_aberto: 'Há um objetivo aberto neste aparelho: espere o desfecho ou cancele a execução.',
  comando_em_voo: 'Há um comando em andamento neste aparelho: espere o desfecho.',
  trabalho_em_curso: 'Há trabalho em execução neste aparelho (instalação, autenticação ou objetivo): espere terminar.',
  instancia_da_configuracao: 'Este aparelho vem do config.yaml: ele sai reduzindo instances.count, não por aqui.',
  aparelho_de_worker: 'Aparelho de worker remoto: o AVD vive naquela máquina e não se aposenta por aqui.',
  avd_nao_apagado: 'O AVD não pôde ser apagado do disco, e o aparelho continua no parque. Veja o log do servidor.',
};

export function recusaDaAposentadoria(e: ApiError): RecusaNaTela {
  return {
    code: e.code,
    titulo: 'Não foi possível aposentar',
    passo: PASSO_DA_APOSENTADORIA[e.code] ?? null,
    mensagem: e.message,
  };
}

/** O formulário de "Criar aparelho", como a pessoa o preenche. */
export interface RascunhoDeAparelho {
  appId: string;
  imagem: string;
  ramMb: string;
  ligar: boolean;
}

export type ErrosDoRascunho = Partial<Record<'imagem' | 'ramMb', string>>;

/** O formato do SDK: `system-images;android-34;google_apis_playstore;x86_64` (quatro partes separadas por `;`). */
const IMAGEM_DO_SDK = /^system-images;android-[^;\s]+;[^;\s]+;[^;\s]+$/;
export const RAM_MIN_MB = 1024;
export const RAM_MAX_MB = 32768;

/** Valida e monta o corpo do `POST /api/instances`. Vazio = o padrão deste servidor (o backend não recebe o campo). */
export function montarPedido(r: RascunhoDeAparelho, idempotencyKey: string): { erros: ErrosDoRascunho; corpo: InstanceProvisionRequest | null } {
  const erros: ErrosDoRascunho = {};
  const imagem = r.imagem.trim();
  if (imagem && !IMAGEM_DO_SDK.test(imagem)) {
    erros.imagem = 'Use o formato do SDK: system-images;android-34;google_apis_playstore;x86_64.';
  }
  const ramTexto = r.ramMb.trim();
  let ram: number | null = null;
  if (ramTexto) {
    if (!/^\d+$/.test(ramTexto)) erros.ramMb = 'Use um número inteiro de MB.';
    else {
      ram = Number(ramTexto);
      if (ram < RAM_MIN_MB || ram > RAM_MAX_MB) erros.ramMb = `Entre ${RAM_MIN_MB.toLocaleString('pt-BR')} e ${RAM_MAX_MB.toLocaleString('pt-BR')} MB.`;
    }
  }
  if (Object.keys(erros).length > 0) return { erros, corpo: null };
  return {
    erros,
    corpo: {
      app_id: r.appId || null,
      system_image: imagem || null,
      ram_mb: ram,
      create: true,
      start: r.ligar,
      idempotency_key: idempotencyKey,
    },
  };
}
