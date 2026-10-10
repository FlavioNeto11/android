import { ArrowLeft, ArrowRight, MonitorSmartphone, Smartphone, Workflow } from 'lucide-react';
import { profileAvatarUrl } from '../../api/client';
import { Avatar } from '../../components/Avatar';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { SeloDeTeste } from '../../components/SeloDeTeste';
import { StatusBadge } from '../../components/StatusBadge';
import { PROFILE_STATUS, metaOf } from '../../lib/status';
import { tempoRelativo, useNow } from '../../lib/time';
import { useUiStore } from '../../store/ui';
import { guardarRascunho, rascunhoDaPersona } from '../operacao/criar';
import type { Aba } from './abas';
import { estadoDaSessao } from './estadoSessao';
import { handleDe, idsDosAparelhos, nomeDe, type Pessoa } from './pessoa';
import styles from './Profiles.module.css';

/**
 * O cabeçalho ÚNICO da persona: foto, nome, @, estado, aparelho vinculado e a ação principal. A Visão geral não
 * repete nada disto (ali ficam só os atributos). O estado é acionável: "Não verificada" leva a quem verifica (a guia
 * Contas e acesso, onde moram os botões de verdade), e "Conectado" diz desde quando a conta foi confirmada.
 * Nada aqui muda a persona: bloquear, pausar e remover ficam na lista e na guia Configurações.
 */
export function PersonaHeader({ profile, onBack, irPara }: {
  profile: Pessoa;
  onBack: () => void;
  irPara: (aba: Aba) => void;
}) {
  const now = useNow();
  const openFocus = useUiStore((s) => s.openFocus);
  const navegar = useUiStore((s) => s.navegar);
  const nome = nomeDe(profile);
  const handle = handleDe(profile);
  const aparelhos = idsDosAparelhos(profile);
  const principal = profile.instance_id;
  const sessao = estadoDaSessao(profile.session);

  return (
    <header className={styles.cabecalho}>
      <div>
        <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>Personas</Button>
      </div>
      <div className={styles.cabecalhoLinha}>
        <Avatar src={profileAvatarUrl(profile.id, profile.has_avatar)} name={nome} size={64} />
        <div className={styles.cabecalhoCorpo}>
          <h1 className={styles.title}>{nome}</h1>
          <p className={styles.cabecalhoHandle}>{handle ? `@${handle}` : 'Sem conta de cadastro'}</p>
          <div className={styles.cabecalhoSelos}>
            <SeloDeTeste teste={profile.teste} />
            {profile.status !== 'active' ? <StatusBadge meta={metaOf(PROFILE_STATUS, profile.status)} /> : null}
            {handle ? (
              sessao.acao ? (
                <button type="button" className={styles.estadoAcionavel}
                        title={`${sessao.meta.description ?? sessao.meta.label} Abre a guia Contas e acesso.`}
                        onClick={() => irPara(sessao.acao!.guia)}>
                  <StatusBadge meta={sessao.meta} />
                  <span className={styles.estadoAcao}>{sessao.acao.rotulo} <ArrowRight size={13} aria-hidden /></span>
                </button>
              ) : (
                <StatusBadge meta={sessao.meta}
                             label={sessao.confirmadaEm ? `${sessao.meta.label} · confirmada ${tempoRelativo(sessao.confirmadaEm, now)}` : undefined} />
              )
            ) : null}
            <Badge icon={Smartphone} tone={principal ? 'neutral' : 'muted'}>
              {principal
                ? `${principal}${aparelhos.length > 1 ? ` (principal) +${aparelhos.length - 1}` : ''}`
                : 'Sem aparelho vinculado'}
            </Badge>
          </div>
        </div>
        <div className={styles.cabecalhoAcoes}>
          <Button variant="primary" icon={MonitorSmartphone}
                  disabledReason={principal ? null : 'Vincule um aparelho a esta persona (guia Contas e aparelhos).'}
                  onClick={() => principal && openFocus(principal)}>
            Abrir no aparelho
          </Button>
          <Button variant="outline" icon={Workflow} disabledReason={profile.status === 'active' ? null : 'A persona não está ativa: só uma persona ativa entra numa operação.'}
                  onClick={() => { guardarRascunho(rascunhoDaPersona(profile.id)); navegar({ tela: 'operacoes', segmentos: ['nova'] }); }}>
            Operar com esta persona
          </Button>
        </div>
      </div>
    </header>
  );
}
