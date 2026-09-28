import {
  ArrowLeft, BrainCircuit, ClipboardCheck, Image as ImageIcon, KeyRound, ListChecks, MessageSquare, Settings2,
  Smartphone, Sparkles, UserRound,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, profileAvatarUrl } from '../../api/client';
import { Avatar } from '../../components/Avatar';
import { Button } from '../../components/Button';
import { Page } from '../../components/Page';
import { StatusBadge } from '../../components/StatusBadge';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { ACCOUNT_SESSION_STATUS, PROFILE_STATUS, metaOf } from '../../lib/status';
import { type Aba } from './abas';
import { AbaAparelho } from './GuiaAparelhos';
import { AbaAprovacoes } from './GuiaAprovacoes';
import { AbaConfiguracoes } from './GuiaConfiguracoes';
import { AbaContasEAcesso } from './GuiaContas';
import { AbaExecucoes } from './GuiaExecucoes';
import { AbaHabilidades } from './GuiaHabilidades';
import { AbaImagens } from './GuiaImagens';
import { AbaInteracoes } from './GuiaInteracoes';
import { AbaMemoria } from './GuiaMemoria';
import { AbaPersona } from './GuiaPersona';
import { VisaoGeral } from './GuiaVisaoGeral';
import { handleDe, nomeDe, type Pessoa } from './pessoa';
import { AppSwitcher, useContas } from './ProfileAccounts';
import styles from './Profiles.module.css';

/**
 * Tela de uma persona: a pessoa e as guias dela. Cada guia vive no seu arquivo e carrega o que precisa quando é
 * aberta, e só então. "Contas e acesso" funde as antigas Contas e Autenticação; "Imagens" é a galeria; "Aparelhos"
 * é o vínculo (um só até a onda E2).
 */
export function ProfileDetail({ profile, onBack, onChanged, abaInicial = 'visao' }: {
  profile: Pessoa;
  onBack: () => void;
  onChanged: () => Promise<void>;
  /** Guia de abertura (ex.: pedida pelo Foco com `openPersona(id, 'contas')`). */
  abaInicial?: Aba;
}) {
  const [aba, setAba] = useState<Aba>(abaInicial);
  // Item 12.2: a persona tem contas em vários apps; o filtro de app vale para Memória e Interações.
  const [contas, recarregarContas, erroContas] = useContas(profile.id);
  const [appFiltro, setAppFiltro] = useState<string | null>(null);
  const [novaConta, setNovaConta] = useState(0);
  const [pendentes, setPendentes] = useState<number | null>(null);

  useEffect(() => {
    let vivo = true;
    void api.listApprovals('pending', profile.id)
      .then((a) => vivo && setPendentes(a.length))
      .catch(() => vivo && setPendentes(null));
    return () => {
      vivo = false;
    };
  }, [profile.id, aba]);

  const nome = nomeDe(profile);
  const handle = handleDe(profile);
  const abas: TabDef<Aba>[] = [
    { id: 'visao', label: 'Visão geral', icon: UserRound },
    { id: 'persona', label: 'Persona', icon: Sparkles },
    { id: 'contas', label: 'Contas e acesso', icon: KeyRound, count: contas?.length ?? null },
    { id: 'imagens', label: 'Imagens', icon: ImageIcon, count: profile.images?.length ?? null },
    { id: 'aparelhos', label: 'Aparelhos', icon: Smartphone },
    { id: 'memoria', label: 'Memória', icon: BrainCircuit },
    { id: 'interacoes', label: 'Interações', icon: MessageSquare },
    { id: 'habilidades', label: 'Habilidades', icon: Sparkles },
    { id: 'aprovacoes', label: 'Aprovações', icon: ClipboardCheck, count: pendentes, alert: !!pendentes },
    { id: 'execucoes', label: 'Execuções', icon: ListChecks },
    { id: 'config', label: 'Configurações', icon: Settings2 },
  ];

  return (
    <Page>
      <div className={styles.header}>
        <div>
          <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>Personas</Button>
          <div className={styles.identidade}>
            <Avatar src={profileAvatarUrl(profile.id)} name={nome} size={56} />
            <div>
              <h1 className={styles.title}>{nome}</h1>
              <p className={styles.lead}>
                {handle ? `@${handle}` : 'sem conta de cadastro'} · {profile.instance_id || 'sem aparelho vinculado'}
              </p>
            </div>
          </div>
        </div>
        <div className={styles.headerButtons}>
          {profile.status !== 'active' ? <StatusBadge meta={metaOf(PROFILE_STATUS, profile.status)} /> : null}
          {handle ? <StatusBadge meta={metaOf(ACCOUNT_SESSION_STATUS, profile.session.status)} /> : null}
        </div>
      </div>

      {contas && contas.length ? (
        <AppSwitcher contas={contas} valor={appFiltro} onChange={setAppFiltro}
                     onAdicionar={() => { setAba('contas'); setNovaConta((n) => n + 1); }} />
      ) : null}
      <Tabs tabs={abas} active={aba} onChange={setAba} idBase={`perfil-${profile.id}`} label="Guias da persona" />
      <TabPanel idBase={`perfil-${profile.id}`} id={aba}>
        {aba === 'visao' ? <VisaoGeral profile={profile} contas={contas} irPara={setAba} /> : null}
        {aba === 'persona' ? <AbaPersona profile={profile} onChanged={onChanged} /> : null}
        {aba === 'contas' ? (
          <AbaContasEAcesso key={novaConta} profile={profile} contas={contas} erro={erroContas}
                            recarregar={recarregarContas} onChanged={onChanged} abrirFormulario={novaConta > 0} />
        ) : null}
        {aba === 'imagens' ? <AbaImagens profile={profile} onChanged={onChanged} /> : null}
        {aba === 'aparelhos' ? <AbaAparelho profile={profile} onChanged={onChanged} /> : null}
        {aba === 'memoria' ? <AbaMemoria profile={profile} appId={appFiltro} /> : null}
        {aba === 'interacoes' ? <AbaInteracoes profile={profile} appId={appFiltro} /> : null}
        {aba === 'habilidades' ? <AbaHabilidades profile={profile} /> : null}
        {aba === 'aprovacoes' ? <AbaAprovacoes profile={profile} /> : null}
        {aba === 'execucoes' ? <AbaExecucoes profile={profile} /> : null}
        {aba === 'config' ? <AbaConfiguracoes profile={profile} onChanged={onChanged} /> : null}
      </TabPanel>
    </Page>
  );
}
