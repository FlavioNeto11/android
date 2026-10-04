// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { PortalContatosBusca, PortalExclusaoResultado } from '../../api/types';
import { ConfirmHost } from '../../components/Confirm';
import { FakeBackend, allByRole, apiError, byRole, click, installBrowserStubs, json, setValue, text, waitFor } from '../../test/harness';
import { PrivacidadeSection } from './PrivacidadeSection';

// 29.83: a tela de exclusão a pedido do titular. Provedor falso (FakeBackend), telefone fictício; nenhuma rede real.

const BUSCA = /^\/api\/portal\/contatos\/busca$/;
const EXCLUIR = /^\/api\/portal\/contatos\/excluir$/;
const TELEFONE = '11 90000-0001';

const DOIS: PortalContatosBusca = {
  contatos: [
    { id: 7, criado_em: '2026-10-01T15:30:00Z', estado: 'entregue', final: '0001' },
    { id: 9, criado_em: '2026-10-02T09:05:00Z', estado: 'retido', final: '0001' },
  ],
};

const RESULTADO: PortalExclusaoResultado = {
  apagados: [7], mantidos: [{ id: 9, motivo: 'em_envio' }], inexistentes: [], mensagens_apagadas: 2,
  mensagens_a_mao: [{ contato_id: 7, enviada_em: '2026-09-20T12:00:00Z' }], sem_canal: false,
};

let root: Root;
let container: HTMLElement;
let backend: FakeBackend;

async function render(): Promise<void> {
  await act(async () => { root.render(<><PrivacidadeSection /><ConfirmHost /></>); });
}

const campo = () => byRole('textbox', /Telefone com DDD/, container) as HTMLInputElement;
const caixa = (id: number) => byRole('checkbox', `Contato nº ${id}`, container) as HTMLInputElement;
const rotulosDeLista = () => allByRole('checkbox', /Contato nº/, container).map((c) => c.getAttribute('aria-label'));

function radio(rotulo: RegExp): HTMLInputElement {
  const label = Array.from(container.querySelectorAll('fieldset label')).find((l) => rotulo.test(l.textContent ?? ''));
  const input = label?.querySelector('input');
  if (!input) throw new Error(`Sem rádio ${String(rotulo)}`);
  return input;
}

async function buscar(telefone = TELEFONE): Promise<void> {
  await setValue(campo(), telefone);
  await click(byRole('button', /^Buscar/, container));
}

async function marcarOrigem(rotulo: RegExp): Promise<void> {
  await click(radio(rotulo));
}

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  backend = new FakeBackend();
  backend.install();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe('Exclusão a pedido do titular', () => {
  it('telefone curto ou longo demais é barrado no campo e nada vai à rede', async () => {
    await render();
    await buscar('1190000');
    expect(text(container)).toContain('Informe o telefone com DDD');
    await buscar('55 11 90000-0001 99');
    expect(text(container)).toContain('Informe o telefone com DDD');
    expect(backend.calls).toHaveLength(0);
    const el = campo();
    expect(el.type).toBe('tel');
    expect(el.getAttribute('inputmode')).toBe('tel');
    expect(el.getAttribute('autocomplete')).toBe('off');
  });

  it('busca por POST com só os dígitos e mostra o final do telefone, sem nome nem mensagem', async () => {
    backend.on('POST', BUSCA, () => json(DOIS));
    await render();
    await buscar();
    await waitFor(() => expect(rotulosDeLista()).toEqual(['Contato nº 7', 'Contato nº 9']));
    expect(backend.callsTo('POST', BUSCA)[0]?.body).toEqual({ telefone: '11900000001' });
    // A busca não leva o número à URL.
    expect(backend.calls.every((c) => !c.path.includes('1190000') && c.query.toString() === '')).toBe(true);
    const t = text(container);
    expect(t).toContain('telefone final 0001');
    expect(t).toContain('Entregue');
    expect(t).toContain('Retido (excesso na hora)');
    expect(t).toMatch(/01\/10\/2026 \d{2}:\d{2}/);
    expect(t).not.toMatch(/nome|empresa|mensagem:/i);
  });

  it('lista vazia diz que não há contato e não oferece exclusão', async () => {
    backend.on('POST', BUSCA, () => json({ contatos: [] }));
    await render();
    await buscar();
    await waitFor(() => expect(text(container)).toContain('Nenhum contato com esse telefone.'));
    expect(allByRole('button', /Excluir selecionados/, container)).toHaveLength(0);
  });

  it('erro da API aparece num aviso com a mensagem', async () => {
    backend.on('POST', BUSCA, () => apiError(422, 'telefone_invalido', 'Telefone inválido.'));
    await render();
    await buscar();
    await waitFor(() => expect(text(container)).toContain('Telefone inválido.'));
    expect(text(container)).toContain('Não foi possível concluir');
  });

  it('excluir exige seleção e a origem do pedido; "Selecionar todos" marca tudo', async () => {
    backend.on('POST', BUSCA, () => json(DOIS));
    await render();
    await buscar();
    await waitFor(() => expect(rotulosDeLista()).toHaveLength(2));
    const excluir = () => byRole('button', /Excluir selecionados/, container) as HTMLButtonElement;
    expect(excluir().disabled).toBe(true);
    await click(caixa(7));
    expect(excluir().disabled).toBe(true);           // falta a origem
    await marcarOrigem(/^Telefone$/);
    expect(excluir().disabled).toBe(false);
    await click(caixa(7));
    expect(excluir().disabled).toBe(true);           // origem sem seleção
    await click(byRole('button', /Selecionar todos/, container));
    expect(caixa(7).checked && caixa(9).checked).toBe(true);
    expect(excluir().disabled).toBe(false);
    // A escolha é um grupo de rádios com legenda, sem campo de texto livre.
    expect(container.querySelector('fieldset legend')?.textContent).toBe('O pedido chegou por');
    expect(container.querySelectorAll('fieldset input[type="radio"]')).toHaveLength(3);
    expect(container.querySelectorAll('textarea')).toHaveLength(0);
  });

  it('o confirm diz o que será e o que não será apagado; cancelar não chama a API', async () => {
    backend.on('POST', BUSCA, () => json(DOIS));
    await render();
    await buscar();
    await waitFor(() => expect(rotulosDeLista()).toHaveLength(2));
    await click(caixa(7));
    await click(caixa(9));
    await marcarOrigem(/Formulário do site/);
    await click(byRole('button', /Excluir selecionados/, container));
    const dialogo = await waitFor(() => byRole('dialog', /Apagar definitivamente\?/));
    const t = text(dialogo);
    expect(t).toContain('Será apagado');
    expect(t).toContain('Excluir estes 2 contatos apaga os dados deles na Central');
    expect(t).toContain('Também serão apagados do seu Telegram os avisos destes contatos e as suas respostas a esses avisos');
    expect(t).toContain('O que não der para apagar sozinho aparece numa lista com a hora');
    expect(t).toContain('nome, empresa, telefone e mensagem');
    expect(t).toContain('até 48 horas depois do envio');
    expect(t).toContain('Não será apagado');
    expect(t).toContain('cópias de segurança');
    expect(t).toContain('cerca de duas semanas');
    expect(t).toContain('mais de 48 horas');
    expect(t).toContain('Não há como desfazer');
    await click(byRole('button', /^Voltar$/, dialogo));
    await waitFor(() => expect(allByRole('dialog', /.*/)).toHaveLength(0));
    expect(backend.callsTo('POST', EXCLUIR)).toHaveLength(0);
  });

  it('confirmar chama a exclusão com os ids e o pedido_por, mostra o resultado e refaz a busca', async () => {
    let buscas = 0;
    backend.on('POST', BUSCA, () => {
      buscas += 1;
      return json(buscas === 1 ? DOIS : { contatos: [DOIS.contatos[1]] });
    });
    backend.on('POST', EXCLUIR, () => json(RESULTADO));
    await render();
    await buscar();
    await waitFor(() => expect(rotulosDeLista()).toHaveLength(2));
    await click(caixa(7));
    await click(caixa(9));
    await marcarOrigem(/Outro/);
    await click(byRole('button', /Excluir selecionados/, container));
    const dialogo = await waitFor(() => byRole('dialog', /Apagar definitivamente\?/));
    await click(byRole('button', /^Apagar definitivamente$/, dialogo));
    await waitFor(() => expect(backend.callsTo('POST', EXCLUIR)).toHaveLength(1));
    expect(backend.callsTo('POST', EXCLUIR)[0]?.body).toEqual({ ids: [7, 9], pedido_por: 'outro' });

    await waitFor(() => expect(rotulosDeLista()).toEqual(['Contato nº 9']));   // a busca foi refeita
    expect(buscas).toBe(2);
    const estado = byRole('status', /1 contato apagado/, container);
    const t = text(estado);
    expect(t).toContain('2 mensagens do bot apagadas');
    expect(t).toContain('Contato nº 7, enviada (ou que pode ter saído) em 20/09/2026');
    expect(t).toContain('Contato nº 9: a mensagem estava saindo agora; tente de novo em um minuto');
    // O que sobrou não herda a marca de quem foi apagado.
    expect(caixa(9).checked).toBe(true);
  });

  it('inexistentes, canal sem exclusão e sem canal ganham texto próprio', async () => {
    backend.on('POST', BUSCA, () => json(DOIS));
    backend.on('POST', EXCLUIR, () => json({
      apagados: [], mantidos: [{ id: 9, motivo: 'canal_sem_exclusao' }, { id: 7, motivo: 'falhou' }], inexistentes: [12],
      mensagens_apagadas: 0, mensagens_a_mao: [], sem_canal: true,
    } satisfies PortalExclusaoResultado));
    await render();
    await buscar();
    await waitFor(() => expect(rotulosDeLista()).toHaveLength(2));
    await click(caixa(9));
    await marcarOrigem(/Formulário do site/);
    await click(byRole('button', /Excluir selecionados/, container));
    const dialogo = await waitFor(() => byRole('dialog', /Apagar definitivamente\?/));
    await click(byRole('button', /^Apagar definitivamente$/, dialogo));
    const estado = await waitFor(() => byRole('status', /0 contatos apagados/, container));
    const t = text(estado);
    expect(t).toContain('use o procedimento manual de docs/operacao.md');
    expect(t).toContain('nada foi apagado deste contato, tente de novo');
    expect(t).toContain('Contato nº 12: já não existiam.');
    expect(t).toContain('Sem canal de aviso nesta instalação.');
  });

  it('falha na exclusão mostra o aviso e não diz que apagou', async () => {
    backend.on('POST', BUSCA, () => json(DOIS));
    backend.on('POST', EXCLUIR, () => apiError(500, 'erro_interno', 'Falha ao apagar.'));
    await render();
    await buscar();
    await waitFor(() => expect(rotulosDeLista()).toHaveLength(2));
    await click(caixa(7));
    await marcarOrigem(/Telefone/);
    await click(byRole('button', /Excluir selecionados/, container));
    const dialogo = await waitFor(() => byRole('dialog', /Apagar definitivamente\?/));
    await click(byRole('button', /^Apagar definitivamente$/, dialogo));
    await waitFor(() => expect(text(container)).toContain('Falha ao apagar.'));
    expect(allByRole('status', /apagado/, container)).toHaveLength(0);
  });
});
