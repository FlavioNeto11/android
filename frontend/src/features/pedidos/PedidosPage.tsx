import { Bell, ListChecks } from 'lucide-react';
import { Page } from '../../components/Page';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { useUiStore } from '../../store/ui';
import { CaixaDeAvisos } from './CaixaDeAvisos';
import { DetalheDoPedido } from './DetalheDoPedido';
import { ListaDePedidos } from './ListaDePedidos';
import { usePedidosStore } from './store';
import styles from './Pedidos.module.css';

type Aba = 'pedidos' | 'avisos';
const ID = 'pedidos';

/**
 * Pedidos (Fase 28, item 28.9): o objetivo que dura. `#/pedidos` é a lista (e a caixa de avisos, na guia `?aba=avisos`);
 * `#/pedidos/<id>` é o detalhe. A URL manda (ADR-062): tela, pedido aberto, guia e filtros moram no hash.
 */
export function PedidosPage() {
  const id = useUiStore((s) => s.rota.segmentos[0]);
  const abaDoLink = useUiStore((s) => s.rota.query.aba);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const naoLidos = usePedidosStore((s) => s.naoLidos);
  if (id) return <DetalheDoPedido key={id} id={id} />;

  const aba: Aba = abaDoLink === 'avisos' ? 'avisos' : 'pedidos';
  const tabs: TabDef<Aba>[] = [
    { id: 'pedidos', label: 'Pedidos', icon: ListChecks },
    { id: 'avisos', label: 'Avisos', icon: Bell, count: naoLidos, alert: (naoLidos ?? 0) > 0 },
  ];
  return (
    <Page title="Pedidos"
          lead="Um pedido é o objetivo que dura: gera uma ocorrência por data, e cada ocorrência vira uma execução comum. Crie um pelo Comando, com “Repetir ou acompanhar…”.">
      <Tabs tabs={tabs} active={aba} onChange={(a) => trocarQuery(a === 'avisos' ? { aba: 'avisos' } : { aba: undefined })} idBase={ID} label="Pedidos" />
      <TabPanel idBase={ID} id={aba} className={styles.tabBody}>
        {aba === 'pedidos' ? <ListaDePedidos /> : <CaixaDeAvisos />}
      </TabPanel>
    </Page>
  );
}
