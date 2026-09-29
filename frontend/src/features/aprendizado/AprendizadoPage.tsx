import { BookOpen, Flame, Inbox, MessageSquareText } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Page } from '../../components/Page';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { loadJson, saveJson } from '../../lib/storage';
import { AprendidoTab } from './AprendidoTab';
import { useContagemDoAprendizado } from './contagem';
import { FalhasTab } from './FalhasTab';
import { ParaAprovarTab } from './ParaAprovarTab';
import { SinaisTab } from './SinaisTab';
import styles from './Aprendizado.module.css';

type Aba = 'aprovar' | 'aprendido' | 'falhas' | 'sinais';
const ABAS: readonly Aba[] = ['aprovar', 'aprendido', 'falhas', 'sinais'];
const isAba = (v: unknown): v is Aba => typeof v === 'string' && (ABAS as readonly string[]).includes(v);
const ID = 'aprendizado';

/**
 * Aprendizado (ADR-054): o que o sistema aprendeu com as execuções e com quem monitora, e o que ele mais erra.
 *
 * - Para aprovar: a fila do D1 (efeito externo ou texto de pessoa) e "Revisar" (o legado com efeito);
 * - Aprendido: o catálogo unificado, com o estado e as decisões da pessoa;
 * - O que mais falha: o backlog para as sessões de desenvolvimento;
 * - Sinais: os votos e os gestos que viram evidência.
 */
export function AprendizadoPage() {
  const [aba, setAba] = useState<Aba>(() => loadJson('aprendizado.aba', isAba) ?? 'aprovar');
  const pendentes = useContagemDoAprendizado((s) => s.pendentes);

  useEffect(() => {
    void useContagemDoAprendizado.getState().atualizar();
  }, []);

  const trocar = (a: Aba) => {
    setAba(a);
    saveJson('aprendizado.aba', a);
  };

  const tabs: TabDef<Aba>[] = [
    { id: 'aprovar', label: 'Para aprovar', icon: Inbox, count: pendentes, alert: (pendentes ?? 0) > 0 },
    { id: 'aprendido', label: 'Aprendido', icon: BookOpen },
    { id: 'falhas', label: 'O que mais falha', icon: Flame },
    { id: 'sinais', label: 'Sinais', icon: MessageSquareText },
  ];

  return (
    <Page
      title="Aprendizado"
      lead="O que o sistema aprendeu com as execuções e com você. Sem efeito externo e com repetição, ele publica sozinho; com efeito ou texto de pessoa, espera a sua aprovação. Rebaixar é sempre automático."
    >
      <Tabs tabs={tabs} active={aba} onChange={trocar} idBase={ID} label="Aprendizado" />
      <TabPanel idBase={ID} id={aba} className={styles.tabBody}>
        {aba === 'aprovar' ? <ParaAprovarTab /> : null}
        {aba === 'aprendido' ? <AprendidoTab /> : null}
        {aba === 'falhas' ? <FalhasTab /> : null}
        {aba === 'sinais' ? <SinaisTab /> : null}
      </TabPanel>
    </Page>
  );
}
