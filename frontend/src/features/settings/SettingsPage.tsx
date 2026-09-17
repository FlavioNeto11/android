import { AppWindow, Bot, ServerCrash, SlidersHorizontal, Smartphone, Workflow } from 'lucide-react';
import { useState } from 'react';
import appStyles from '../../App.module.css';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { LoadingRegion, Skeleton } from '../../components/Skeleton';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { isString, loadJson, saveJson } from '../../lib/storage';
import { useAppStore } from '../../store/app';
import { reconnectNow } from '../../store/live';
import { AiSection } from './AiSection';
import { AppsSection } from './AppsSection';
import { FlowsRecipesSection } from './FlowsRecipesSection';
import { InstancesSection } from './InstancesSection';
import { LimitsSection } from './LimitsSection';
import styles from './Settings.module.css';

type SectionId = 'apps' | 'instancias' | 'ia' | 'fluxos' | 'limites';

const SECTIONS: TabDef<SectionId>[] = [
  { id: 'apps', label: 'Aplicativos', icon: AppWindow },
  { id: 'instancias', label: 'Instâncias e contas', icon: Smartphone },
  { id: 'ia', label: 'IA', icon: Bot },
  { id: 'fluxos', label: 'Fluxos e receitas', icon: Workflow },
  { id: 'limites', label: 'Limites', icon: SlidersHorizontal },
];

function isSection(v: unknown): v is SectionId {
  return isString(v) && SECTIONS.some((s) => s.id === v);
}

export function SettingsPage() {
  const hydrated = useAppStore((s) => s.hydrated);
  const connStatus = useAppStore((s) => s.conn.status);
  const [section, setSection] = useState<SectionId>(() => loadJson('settingsSection', isSection) ?? 'apps');

  const change = (id: SectionId) => {
    setSection(id);
    saveJson('settingsSection', id);
  };

  return (
    <div className={`${appStyles.page} ${appStyles.pageNarrow}`}>
      <div className={appStyles.pageHeader}>
        <div>
          <h1 className={appStyles.pageTitle}>Configuração</h1>
          <p className={appStyles.pageLead}>Aplicativos que a IA opera, associação de instâncias e contas, status da IA, fluxos e receitas aprendidos e limites de segurança.</p>
        </div>
      </div>

      <Card>
        <Tabs tabs={SECTIONS} active={section} onChange={change} idBase="cfg" label="Seções de configuração" />
        <TabPanel idBase="cfg" id={section} className={styles.sectionBody}>
          {!hydrated && section !== 'ia' ? (
            connStatus === 'connecting' ? (
              <LoadingRegion label="Carregando a configuração…">
                <Skeleton width="50%" height={16} />
                <Skeleton height={80} radius={8} style={{ marginTop: 12 }} />
                <Skeleton height={80} radius={8} style={{ marginTop: 12 }} />
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
      </Card>
    </div>
  );
}
