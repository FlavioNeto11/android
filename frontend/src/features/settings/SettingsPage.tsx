import { AppWindow, Bot, ServerCrash, SlidersHorizontal, Smartphone, Workflow } from 'lucide-react';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Page } from '../../components/Page';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { useAppStore } from '../../store/app';
import { reconnectNow } from '../../store/live';
import { useUiStore } from '../../store/ui';
import { AiSection } from './AiSection';
import { AppsSection } from './AppsSection';
import { FlowsRecipesSection } from './FlowsRecipesSection';
import { InstancesSection } from './InstancesSection';
import { LimitsSection } from './LimitsSection';
import styles from './Settings.module.css';

type SectionId = 'apps' | 'instancias' | 'ia' | 'fluxos' | 'limites';

const SECTIONS: TabDef<SectionId>[] = [
  { id: 'apps', label: 'Aplicativos', icon: AppWindow },
  { id: 'instancias', label: 'Aparelhos e contas', icon: Smartphone },
  { id: 'ia', label: 'IA', icon: Bot },
  { id: 'fluxos', label: 'Fluxos e receitas', icon: Workflow },
  { id: 'limites', label: 'Limites', icon: SlidersHorizontal },
];

/** Como a guia aparece no link (`#/configuracao?aba=aplicativos`); "Aplicativos" é a padrão e não entra nele. */
const ABA_NO_LINK: Record<SectionId, string> = {
  apps: 'aplicativos', instancias: 'instancias', ia: 'ia', fluxos: 'fluxos', limites: 'limites',
};

function secaoDoLink(aba: string | undefined): SectionId {
  return (Object.keys(ABA_NO_LINK) as SectionId[]).find((id) => ABA_NO_LINK[id] === aba) ?? 'apps';
}

/**
 * Configuração segue o contrato de página (design §9.2): abas NA página, como Aplicativos, e cada seção em cartões
 * próprios (`PageSection`). Antes era a única tela encaixotada — um Card com as abas dentro, estreitado a 1280 px —
 * e sobrava um terço da tela vazio ao lado.
 */
export function SettingsPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const connStatus = useAppStore((s) => s.conn.status);
  // A guia vem do link: `#/configuracao?aba=fluxos`. Sem `aba`, o link antigo `#/configuracao` continua abrindo
  // Aplicativos (antes a guia ficava só no localStorage, sem link que a levasse a alguém).
  const aba = useUiStore((s) => s.rota.query.aba);
  const trocarQuery = useUiStore((s) => s.trocarQuery);
  const section = secaoDoLink(aba);

  const change = (id: SectionId) => trocarQuery({ aba: id === 'apps' ? undefined : ABA_NO_LINK[id] }, 'replace');

  return (
    <Page
      title="Configuração"
      lead="Aplicativos que a IA opera, associação de instâncias e contas, status da IA, fluxos e receitas aprendidos e limites de segurança."
    >
      <Tabs tabs={SECTIONS} active={section} onChange={change} idBase="cfg" label="Seções de configuração" />
      <TabPanel idBase="cfg" id={section} className={styles.panel}>
        {!hydrated && section !== 'ia' ? (
          connStatus === 'connecting' ? (
            <LoadingRegion label="Carregando a configuração…" className={styles.stack}>
              <Skeleton width="50%" height={16} />
              <Skeleton height={80} radius={8} />
              <Skeleton height={80} radius={8} />
            </LoadingRegion>
          ) : (
            <EmptyState icon={ServerCrash} tone="danger" title="Configuração indisponível" hint="Inicie o backend em 127.0.0.1:8000. A página carrega sozinha quando a conexão voltar." actions={<Button variant="outline" onClick={reconnectNow}>Tentar agora</Button>}>
              Sem conexão com o backend.
            </EmptyState>
          )
        ) : section === 'apps' ? (
          <AppsSection />
        ) : section === 'instancias' ? (
          <InstancesSection />
        ) : section === 'ia' ? (
          <AiSection />
        ) : section === 'fluxos' ? (
          <FlowsRecipesSection />
        ) : (
          <LimitsSection />
        )}
      </TabPanel>
    </Page>
  );
}
