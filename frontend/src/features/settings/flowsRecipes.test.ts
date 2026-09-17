import { describe, expect, it } from 'vitest';
import { RECIPE_STATUS, recipeToggleTarget, scrollText, selectorText, shadowText, splitTemplate } from './flowsRecipes';

describe('splitTemplate — marcadores do comando-modelo', () => {
  it('separa texto e {marcadores} sem perder nenhum caractere', () => {
    const template = 'Abra o QA Messenger, fale com {recipient} e envie “{message_template}”.';
    const parts = splitTemplate(template);
    expect(parts.filter((p) => p.placeholder).map((p) => p.text)).toEqual(['{recipient}', '{message_template}']);
    expect(parts.map((p) => p.text).join('')).toBe(template);
  });

  it('aceita marcador no começo/fim e ignora chaves vazias ou soltas', () => {
    expect(splitTemplate('{a} e {b}')).toEqual([
      { text: '{a}', placeholder: true }, { text: ' e ', placeholder: false }, { text: '{b}', placeholder: true },
    ]);
    expect(splitTemplate('sem marcadores {} { solto')).toEqual([{ text: 'sem marcadores {} { solto', placeholder: false }]);
    expect(splitTemplate('')).toEqual([]);
  });
});

describe('receitas', () => {
  it('status: Ativa / Quarentena (+ Substituída, que o contrato também prevê)', () => {
    expect(RECIPE_STATUS.active.label).toBe('Ativa');
    expect(RECIPE_STATUS.quarantined.label).toBe('Quarentena');
    expect(RECIPE_STATUS.superseded.label).toBe('Substituída');
  });

  it('botão: ativa → quarentena, quarentena → ativa, substituída → sem ação', () => {
    expect(recipeToggleTarget('active')).toBe('quarantined');
    expect(recipeToggleTarget('quarantined')).toBe('active');
    expect(recipeToggleTarget('superseded')).toBeNull();
  });

  it('concordância em modo sombra só aparece com total > 0', () => {
    expect(shadowText({ shadow_agree: 12, shadow_total: 15 })).toBe('12/15 (80%)');
    expect(shadowText({ shadow_agree: 0, shadow_total: 4 })).toBe('0/4 (0%)');
    expect(shadowText({ shadow_agree: 0, shadow_total: 0 })).toBeNull();
  });

  it('descreve seletores e rolagem de forma legível', () => {
    expect(selectorText({ kind: 'rid', rid: 'com.poc.qamessenger:id/send' })).toBe('rid: resource-id=com.poc.qamessenger:id/send');
    expect(selectorText({ kind: 'rid+text', rid: 'id/row', text: '{recipient}' })).toBe('rid+text: resource-id=id/row · texto="{recipient}"');
    expect(selectorText({ kind: 'desc', desc: 'Enviar' })).toBe('desc: descrição="Enviar"');
    expect(selectorText({ kind: 'text' })).toBe('text');
    expect(scrollText({ direction: 'down', max: 5 })).toBe('rola para baixo até 5× procurando o elemento');
    expect(scrollText(undefined)).toBeNull();
  });
});
