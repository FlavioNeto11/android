import appStyles from '../../App.module.css';
import { CommandPanel } from '../command/CommandPanel';
import { DeviceGrid } from '../devices/DeviceGrid';
import { RunView } from '../runs/RunView';
import { ResumoDoDia } from './ResumoDoDia';

/** Painel: resumo do dia (31.215) → comando → grade de aparelhos → execução selecionada/ativa. */
export function PainelPage() {
  return (
    <div className={appStyles.page}>
      <h1 className="sr-only">Painel</h1>
      <ResumoDoDia />
      <CommandPanel />
      <DeviceGrid />
      <section aria-labelledby="exec-title" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h2 id="exec-title" style={{ fontSize: 'var(--fs-xl)', fontWeight: 650 }}>Execução</h2>
        <RunView showPicker />
      </section>
    </div>
  );
}
