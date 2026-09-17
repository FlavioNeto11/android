import { TriangleAlert, type LucideIcon } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { create } from 'zustand';
import { Button } from './Button';
import { Dialog } from './Dialog';
import { Field, TextArea } from './Field';

export interface ConfirmOptions {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  icon?: LucideIcon;
  /** Mostra um campo de observação opcional (ex.: nota ao resolver um bloqueio). */
  note?: { label: string; placeholder?: string };
}

export interface ConfirmResult {
  confirmed: boolean;
  note: string;
}

interface ConfirmStore {
  current: { options: ConfirmOptions; resolve: (r: ConfirmResult) => void } | null;
  open: (options: ConfirmOptions) => Promise<ConfirmResult>;
  settle: (result: ConfirmResult) => void;
}

const useConfirmStore = create<ConfirmStore>((set, get) => ({
  current: null,
  open: (options) =>
    new Promise<ConfirmResult>((resolve) => {
      get().current?.resolve({ confirmed: false, note: '' }); // um pedido por vez
      set({ current: { options, resolve } });
    }),
  settle: (result) => {
    const cur = get().current;
    if (!cur) return;
    set({ current: null });
    cur.resolve(result);
  },
}));

/** `const { confirmed, note } = await confirm({...})` — usado por toda ação destrutiva ou irreversível. */
export function confirm(options: ConfirmOptions): Promise<ConfirmResult> {
  return useConfirmStore.getState().open(options);
}

export function ConfirmHost() {
  const current = useConfirmStore((s) => s.current);
  const settle = useConfirmStore((s) => s.settle);
  const [note, setNote] = useState('');

  useEffect(() => {
    setNote('');
  }, [current]);

  if (!current) return null;
  const { options } = current;
  const cancel = () => settle({ confirmed: false, note: '' });

  return (
    <Dialog
      open
      onClose={cancel}
      title={options.title}
      icon={options.icon ?? (options.danger ? TriangleAlert : undefined)}
      tone={options.danger ? 'danger' : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={cancel} autoFocus>
            {options.cancelLabel ?? 'Voltar'}
          </Button>
          <Button variant={options.danger ? 'danger' : 'primary'} onClick={() => settle({ confirmed: true, note: note.trim() })}>
            {options.confirmLabel}
          </Button>
        </>
      }
    >
      <div>{options.body}</div>
      {options.note ? (
        <Field label={options.note.label} unit="opcional">
          {({ id, describedBy }) => (
            <TextArea id={id} aria-describedby={describedBy} rows={2} value={note} placeholder={options.note?.placeholder} onChange={(e) => setNote(e.target.value)} />
          )}
        </Field>
      ) : null}
    </Dialog>
  );
}
