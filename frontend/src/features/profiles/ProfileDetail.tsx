import {
  Activity, BrainCircuit, ClipboardCheck, Image as ImageIcon, KeyRound, ListChecks, MessageSquare, Settings2,
  Smartphone, Sparkles, UserRound, Wrench, type LucideIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { Select } from '../../components/Field';
import { Page } from '../../components/Page';
import { TabPanel, Tabs, type TabDef } from '../../components/Tabs';
import { abaAoEscolherSecao, SECOES, secaoDaAba, type Aba, type SecaoId } from './abas';
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
import { PersonaHeader } from './PersonaHeader';
import { idsDosAparelhos, type Pessoa } from './pessoa';
import { AppSwitcher, useContas } from './ProfileAccounts';
import styles from './Profiles.module.css';

const ICONE_DA_SECAO: Record<SecaoId, LucideIcon> = {
  visao: UserRound, perfil: Sparkles, contas: KeyRound, atividade: Activity, avancado: Wrench,
};

/** O que o selo da seção Atividade conta: as aprovações de texto que esperam a pessoa (a guia Aprovações). */
function textoDasAprovacoes(n: number | null): string {
  return `${n} ${n === 1 ? 'aprovação aguardando' : 'aprovações aguardando'} você`;
}

/**
 * Tela de uma persona: o cabeçalho único (`PersonaHeader`), as 5 seções (`abas.ts`) e as guias da seção aberta. Cada
 * guia vive no seu arquivo e carrega o que precisa quando é aberta, e só então. "Contas e acesso" funde as antigas Contas e Autenticação; "Imagens" é a galeria; "Aparelhos"
 * são os N vínculos (onda E2, ADR-043).
 */
export function ProfileDetail({ profile, onBack, onChanged, abaInicial = 'visao', aba: abaControlada, onAbaChange }: {
  profile: Pessoa;
  onBack: () => void;
  onChanged: () => Promise<void>;
  /** Guia de abertura quando a guia não é controlada de fora. */
  abaInicial?: Aba;
  /** Guia controlada pela tela (vem do link `#/personas/<persona>/<guia>`); a troca sai por `onAbaChange`. */
  aba?: Aba;
  onAbaChange?: (aba: Aba) => void;
}) {
  const [abaLocal, setAbaLocal] = useState<Aba>(abaInicial);
  const aba = abaControlada ?? abaLocal;
  // Guias, "ir para" da Visão geral e "+ conta" passam todos por aqui: com a guia no link, cada troca chega à URL.
  const setAba = (a: Aba) => {
    setAbaLocal(a);
    onAbaChange?.(a);
  };
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

  const nAparelhos = idsDosAparelhos(profile).length;
  const secao = secaoDaAba(aba);
  const guias: Record<Aba, TabDef<Aba>> = {
    visao: { id: 'visao', label: 'Visão geral', icon: UserRound },
    persona: { id: 'persona', label: 'Persona', icon: Sparkles },
    contas: { id: 'contas', label: 'Contas e acesso', icon: KeyRound, count: contas?.length ?? null },
    imagens: { id: 'imagens', label: 'Imagens', icon: ImageIcon, count: profile.images?.length ?? null },
    aparelhos: { id: 'aparelhos', label: 'Aparelhos', icon: Smartphone, count: nAparelhos || null },
    memoria: { id: 'memoria', label: 'Memória', icon: BrainCircuit },
    interacoes: { id: 'interacoes', label: 'Interações', icon: MessageSquare },
    habilidades: { id: 'habilidades', label: 'Habilidades', icon: Sparkles },
    aprovacoes: { id: 'aprovacoes', label: 'Aprovações', icon: ClipboardCheck, count: pendentes, alert: !!pendentes },
    execucoes: { id: 'execucoes', label: 'Execuções', icon: ListChecks },
    config: { id: 'config', label: 'Configurações', icon: Settings2 },
  };
  const guiasDaSecao = secao.guias.map((g) => guias[g]);
  // O app só filtra estas duas guias; fora delas a fileira de apps seria ruído sem efeito (e sem explicação).
  const mostraEscopoDeApp = (aba === 'memoria' || aba === 'interacoes') && !!contas && contas.length > 0;
  const conteudo = (
    <>
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
    </>
  );

  return (
    <Page>
      <PersonaHeader profile={profile} onBack={onBack} irPara={setAba} />

      {/* Primeiro nível: 5 seções. A URL guarda a GUIA; a seção sai dela (link antigo `…/memoria` abre Perfil > Memória). */}
      <nav className={styles.navSecoes} aria-label="Seções da persona">
        <div className={styles.navSecoesLista}>
          {SECOES.map((sec) => {
            const Icone = ICONE_DA_SECAO[sec.id];
            const atual = sec.id === secao.id;
            const alerta = sec.id === 'atividade' && !!pendentes;
            return (
              <button key={sec.id} type="button" className={styles.navSecaoBotao}
                      aria-current={atual ? 'page' : undefined}
                      aria-label={alerta ? `${sec.rotulo}, ${textoDasAprovacoes(pendentes)}` : undefined}
                      onClick={() => { if (!atual) setAba(abaAoEscolherSecao(sec, pendentes)); }}>
                <Icone size={14} aria-hidden />
                {sec.rotulo}
                {alerta ? (
                  <>
                    {/* O espaço separa o rótulo do número no texto visível, de que o nome acessível parte (WCAG 2.5.3). */}
                    {' '}
                    <span className={styles.navSecaoContagem} title={textoDasAprovacoes(pendentes)}>{pendentes}</span>
                  </>
                ) : null}
              </button>
            );
          })}
        </div>
        {/* Em tela estreita as seções viram uma lista suspensa: nenhum rótulo é cortado. */}
        <div className={styles.navSecoesSelect}>
          <Select aria-label="Seção da persona" value={secao.id}
                  onChange={(e) => {
                    const alvo = SECOES.find((sec) => sec.id === e.target.value);
                    if (alvo && alvo.id !== secao.id) setAba(abaAoEscolherSecao(alvo, pendentes));
                  }}>
            {SECOES.map((sec) => (
              <option key={sec.id} value={sec.id}>
                {sec.rotulo}{sec.id === 'atividade' && pendentes ? ` (${textoDasAprovacoes(pendentes)})` : ''}
              </option>
            ))}
          </Select>
        </div>
      </nav>

      {/* Segundo nível: as guias da seção aberta (só quando há mais de uma). */}
      {guiasDaSecao.length > 1 ? (
        <div className={styles.navGuias}>
          <Tabs tabs={guiasDaSecao} active={aba} onChange={setAba} idBase={`perfil-${profile.id}`} label={`Guias de ${secao.rotulo}`} />
        </div>
      ) : null}

      {mostraEscopoDeApp && contas ? (
        <AppSwitcher contas={contas} valor={appFiltro} onChange={setAppFiltro}
                     onAdicionar={() => { setAba('contas'); setNovaConta((n) => n + 1); }} />
      ) : null}

      {guiasDaSecao.length > 1 ? (
        <TabPanel idBase={`perfil-${profile.id}`} id={aba}>{conteudo}</TabPanel>
      ) : (
        <div role="region" aria-label={secao.rotulo}>{conteudo}</div>
      )}
    </Page>
  );
}
