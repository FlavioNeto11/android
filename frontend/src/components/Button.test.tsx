// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, expect, it } from 'vitest';
import { byRole, click, installBrowserStubs } from '../test/harness';
import { Button } from './Button';

/**
 * `Button` trata `disabledReason` como o INTERRUPTOR: preenchido, o botão fica bloqueado (Button.tsx:40), mesmo
 * que `disabled` seja falso. Quem passa o motivo de forma incondicional cria um botão que parece ativo, mostra um
 * aviso mentiroso e não faz nada ao ser clicado — foi exatamente isso que aconteceu em seis botões da Fase 5.
 */
let root: Root;
let container: HTMLElement;

beforeAll(() => installBrowserStubs());

beforeEach(() => {
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

async function render(node: React.ReactElement): Promise<void> {
  await act(async () => {
    root.render(node);
  });
}

it('com motivo nulo, o clique chega ao onClick', async () => {
  let cliques = 0;
  await render(<Button disabledReason={null} onClick={() => { cliques += 1; }}>Conectar</Button>);
  await click(byRole('button', /Conectar/i));
  expect(cliques).toBe(1);
});

it('com motivo preenchido, o clique NÃO chega ao onClick e o motivo fica no nome acessível', async () => {
  let cliques = 0;
  await render(
    <Button disabledReason="Vincule um aparelho a esta persona." onClick={() => { cliques += 1; }}>
      Conectar
    </Button>,
  );
  const botao = byRole('button', /Conectar/i);
  expect(botao.getAttribute('aria-disabled')).toBe('true');
  await click(botao);
  expect(cliques).toBe(0);
  expect(botao.textContent).toContain('Vincule um aparelho a esta persona.');
});

/**
 * Varredura do próprio código-fonte. Um `disabledReason` com texto literal fixo é sempre defeito: o botão nunca
 * dispara. Se a intenção for um botão permanentemente indisponível, não se renderiza o botão.
 */
it('nenhuma tela passa disabledReason com texto fixo — isso mataria o botão para sempre', () => {
  const arquivos = import.meta.glob('../features/**/*.tsx', { query: '?raw', import: 'default', eager: true });
  const culpados: string[] = [];
  for (const [caminho, conteudo] of Object.entries(arquivos)) {
    if (caminho.includes('.test.')) continue;
    (conteudo as string).split('\n').forEach((linha, i) => {
      // `disabledReason="algo"` — aspas literais, sem chaves, portanto sem condição.
      if (/disabledReason\s*=\s*["'][^"']/.test(linha)) {
        culpados.push(`${caminho.replace('../', '')}:${i + 1} → ${linha.trim()}`);
      }
    });
  }
  expect(culpados, `Use disabledReason={condicao ? null : 'motivo'}. Encontrado em:\n${culpados.join('\n')}`)
    .toEqual([]);
});
