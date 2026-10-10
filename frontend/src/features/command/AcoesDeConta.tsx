/**
 * Pares (persona, app) do comando cuja credencial não está pronta (v1.132, ADR-087, 31.283). O assistente não faz
 * pergunta sobre senha: mostra o estado da conta e as ações estruturadas, que agem pelos ids da persona e do app.
 * Nenhuma senha nem texto do formulário entra no corpo do refinamento: o preparo vai direto ao cofre e a rodada
 * seguinte só reavalia o estado ("continuar").
 */
import { ExternalLink, KeyRound, RefreshCw, UserRoundPlus } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { AcaoDeContaItem, ProfileAccount } from '../../api/types';
import { Badge } from '../../components/Badge';
import { Button } from '../../components/Button';
import { toastError } from '../../store/toasts';
import { PrepararConta } from '../profiles/ContaPlanejada';
import styles from './AssistenteDoComando.module.css';

const ROTULO_DO_ESTADO: Record<AcaoDeContaItem['estado'], string> = {
  sem_conta: 'sem conta preparada',
  planejada: 'planejada, sem senha',
  falha: 'cadastro com falha',
};

export function AcoesDeConta({ itens, onContinuar, desabilitado = false }: {
  itens: readonly AcaoDeContaItem[];
  /** Reavalia o comando: a rodada seguinte relê o estado das contas. */
  onContinuar: () => void;
  desabilitado?: boolean;
}) {
  return (
    <ul className={styles.contas} aria-label="Contas a preparar">
      {itens.map((it) => (
        <ItemDeConta key={`${it.persona_id}|${it.app_id}|${it.host ?? ''}`} item={it} onContinuar={onContinuar}
                     desabilitado={desabilitado} />
      ))}
    </ul>
  );
}

function ItemDeConta({ item, onContinuar, desabilitado }: {
  item: AcaoDeContaItem; onContinuar: () => void; desabilitado: boolean;
}) {
  const [aberto, setAberto] = useState<null | 'preparar' | 'reutilizar'>(null);
  const [contas, setContas] = useState<ProfileAccount[] | null>(null);
  const [carregando, setCarregando] = useState(false);
  const tem = (a: AcaoDeContaItem['acoes'][number]) => item.acoes.includes(a);

  async function abrir(como: 'preparar' | 'reutilizar') {
    if (aberto === como) { setAberto(null); return; }
    setCarregando(true);
    try {
      // As contas da MESMA pessoa: é de onde a senha pode ser reaproveitada, se a pessoa escolher.
      setContas(await api.listAccounts(item.persona_id));
      setAberto(como);
    } catch (e) {
      toastError('Não foi possível abrir o preparo da conta', e);
    } finally {
      setCarregando(false);
    }
  }

  return (
    <li className={styles.conta}>
      <div className={styles.contaTopo}>
        <strong>{item.persona_nome}</strong>
        <span>· {item.app_nome}{item.host ? ` (${item.host})` : ''}</span>
        <Badge size="sm" tone={item.estado === 'falha' ? 'danger' : 'warning'}>{ROTULO_DO_ESTADO[item.estado]}</Badge>
      </div>
      <p className={styles.contaTexto}>
        Esta persona ainda não possui uma conta {item.app_nome} preparada. Prepare a credencial aqui; o cadastro no
        serviço continua sendo da pessoa.
      </p>
      <div className={styles.contaAcoes}>
        {tem('preparar_credencial') ? (
          <Button size="sm" icon={UserRoundPlus} loading={carregando} disabled={desabilitado}
                  onClick={() => void abrir('preparar')}>
            Preparar credencial para esta persona
          </Button>
        ) : null}
        {tem('usar_credencial_existente') ? (
          <Button size="sm" variant="secondary" icon={KeyRound} loading={carregando} disabled={desabilitado}
                  disabledReason={item.reutilizavel_de.length === 0 ? 'Nenhuma outra conta desta pessoa tem senha guardada.' : null}
                  onClick={() => void abrir('reutilizar')}>
            Usar credencial existente
          </Button>
        ) : null}
        {tem('abrir_contas_e_acesso') ? (
          <a className={styles.contaLink} href={`#/personas/${encodeURIComponent(item.persona_id)}/contas`}
             target="_blank" rel="noopener noreferrer">
            <ExternalLink size={13} aria-hidden /> Abrir Contas e acesso (nova guia)
          </a>
        ) : null}
        {tem('continuar') ? (
          <Button size="sm" variant="ghost" icon={RefreshCw} disabled={desabilitado} onClick={onContinuar}>
            Continuar quando estiver pronta
          </Button>
        ) : null}
      </div>
      {aberto && contas ? (
        <PrepararConta key={aberto} profileId={item.persona_id} contas={contas} appFixo={item.app_id}
                       hostInicial={item.host ?? null} modoInicial={aberto === 'reutilizar' ? 'reutilizar' : 'gerar'}
                       onFechar={() => setAberto(null)}
                       onFeita={() => { setAberto(null); onContinuar(); }} />
      ) : null}
    </li>
  );
}
