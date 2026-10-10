/**
 * Conta planejada (ADR-087, 31.283): o que o painel sabe do ciclo de provisionamento, sem tela.
 *
 * Quatro coisas continuam separadas: a persona, a conta EXTERNA (desejada ou existente), a credencial guardada e a
 * sessão observada no app. `provisioning` só descreve o segundo; "autenticada" é derivada e nunca gravada.
 */
import type { AppConfig, ProfileAccount, ProvisioningEvent, ProvisioningInfo, ProvisioningState } from '../../api/types';

export const TEXTO_DO_CONSENTIMENTO = 'Autorizo a automação a digitar esta senha, só no app/site desta conta';

/** App de navegador: a conta é de um SITE (`host`), e a mesma pessoa pode ter várias, uma por site. */
export function ehNavegador(app: Pick<AppConfig, 'id' | 'package' | 'name'>): boolean {
  return app.id === 'chrome' || /chrome|browser|navegador/i.test(`${app.package} ${app.name}`);
}

/** Central anterior ao v1.132 não manda `provisioning`: a conta vale como confirmada (contrato, seção 6). */
export function provisionamentoDe(c: Pick<ProfileAccount, 'provisioning'>): ProvisioningInfo {
  return c.provisioning ?? {
    state: 'confirmada', desired_handle: null, detail: null, resume_state: null, confirmed_at: null, evidence: null,
    actions: [], authenticated: false,
  };
}

/** Conta ainda no ciclo de preparo: não é "conta real logada" e o servidor recusa conectar, verificar e sair dela. */
export function emPreparo(c: Pick<ProfileAccount, 'provisioning'>): boolean {
  return provisionamentoDe(c).state !== 'confirmada';
}

/** Os passos do ciclo, na ordem em que acontecem (`falha` fica fora: é um desvio, não um passo). */
export const PASSOS: readonly { estado: ProvisioningState; rotulo: string }[] = [
  { estado: 'planejada', rotulo: 'Conta planejada' },
  { estado: 'credencial_preparada', rotulo: 'Credencial preparada' },
  { estado: 'aguardando_cadastro_externo', rotulo: 'Cadastro no serviço' },
  { estado: 'aguardando_verificacao', rotulo: 'Verificação' },
  { estado: 'confirmada', rotulo: 'Conta confirmada' },
];

export const ROTULO_DO_ESTADO: Record<ProvisioningState, string> = {
  planejada: 'Planejada, sem senha',
  credencial_preparada: 'Credencial preparada',
  aguardando_cadastro_externo: 'Aguardando o cadastro no serviço',
  aguardando_verificacao: 'Aguardando a verificação',
  confirmada: 'Confirmada',
  falha: 'Falhou',
};

export const ROTULO_DO_EVENTO: Record<ProvisioningEvent, string> = {
  iniciar_cadastro: 'Iniciar o cadastro',
  enviado: 'Enviei o formulário',
  confirmar: 'Confirmar a conta',
  falhar: 'Marcar como falha',
  retomar: 'Retomar de onde parou',
  cancelar: 'Cancelar a conta',
};

/** O que a pessoa faz agora, em uma frase (o servidor só diz os eventos; a tela explica). */
export const PROXIMO_PASSO: Record<ProvisioningState, string> = {
  planejada: 'Prepare a senha desta conta: gerada pelo sistema, digitada por você ou reaproveitada de outra conta da pessoa.',
  credencial_preparada: 'Com a senha no cofre, inicie o cadastro. O CAPTCHA, o código e o e-mail do serviço são da pessoa.',
  aguardando_cadastro_externo: 'Faça o cadastro no serviço (a automação preenche e dita a senha pelo canal sensível) e avise aqui quando enviar.',
  aguardando_verificacao: 'Confirme a conta assim que o serviço verificar: só vale com evidência.',
  confirmada: '',
  falha: 'Veja o motivo, corrija e retome: a senha e os dados continuam guardados.',
};
