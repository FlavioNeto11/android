import { AppWindow, BookOpen, Flame, Inbox, MessageSquareText } from 'lucide-react';
import { useEffect } from 'react';
import { Page } from '../../components/Page';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { useUiStore } from '../../store/ui';
import { AplicativosTab } from './AplicativosTab';
import { AprendidoTab } from './AprendidoTab';
import { useContagemDoAprendizado } from './contagem';
import { FalhasTab } from './FalhasTab';
import { ParaAprovarTab } from './ParaAprovarTab';
import { SinaisTab } from './SinaisTab';
import styles from './Aprendizado.module.css';

type Aba = 'apps' | 'aprovar' | 'aprendido' | 'falhas' | 'sinais';
const ABAS: readonly Aba[] = ['apps', 'aprovar', 'aprendido', 'falhas', 'sinais'];
const isAba = (v: unknown): v is Aba => typeof v === 'string' && (ABAS as readonly string[]).includes(v);
const ID = 'aprendizado';

/**
 * Aprendizado (ADR-054): o que o sistema aprendeu com as execuções e com quem monitora, e o que ele mais erra.
 *
 * - Aplicativos (a visão inicial, Global → App): um cartão por app e o detalhe de cada um (`?aba=apps&app=<pacote>`);
 * - Para aprovar: a fila do D1 (efeito externo ou texto de pessoa) e "Revisar" (o legado com efeito);
 * - Aprendido: o catálogo unificado, com o estado e as decisões da pessoa;
 * - O que mais falha: o backlog para as sessões de desenvolvimento;
 * - Sinais: os votos e os gestos que viram evidência.
 */
export function AprendizadoPage() {
  // A guia vem do link (`#/aprendizado?aba=falhas`); "Aplicativos" é a padrão (o Global) e não entra nele.
  const abaDoLink = useUiStore((s) => s.rota.query.aba);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const aba: Aba = isAba(abaDoLink) ? abaDoLink : 'apps';
  const pendentes = useContagemDoAprendizado((s) => s.pendentes);

  useEffect(() => {
    void useContagemDoAprendizado.getState().atualizar();
  }, []);

  const trocar = (a: Aba) => trocarQuery({ aba: a === 'apps' ? undefined : a, app: undefined }, 'replace');

  const tabs: TabDef<Aba>[] = [
    { id: 'apps', label: 'Aplicativos', icon: AppWindow },
    { id: 'aprovar', label: 'Para aprovar', icon: Inbox, count: pendentes, alert: (pendentes ?? 0) > 0 },
    { id: 'aprendido', label: 'Aprendido', icon: BookOpen },
    { id: 'falhas', label: 'O que mais falha', icon: Flame },
    { id: 'sinais', label: 'Sinais', icon: MessageSquareText },
  ];

  return (
    <Page
      title="Aprendizado"
      lead="O que o sistema aprendeu com as execuções e com você. O que tem efeito fora do sistema espera a sua aprovação."
    >
      <Tabs tabs={tabs} active={aba} onChange={trocar} idBase={ID} label="Aprendizado" />
      <TabPanel idBase={ID} id={aba} className={styles.tabBody}>
        {aba === 'apps' ? <AplicativosTab /> : null}
        {aba === 'aprovar' ? <ParaAprovarTab /> : null}
        {aba === 'aprendido' ? <AprendidoTab /> : null}
        {aba === 'falhas' ? <FalhasTab /> : null}
        {aba === 'sinais' ? <SinaisTab /> : null}
      </TabPanel>
    </Page>
  );
}
