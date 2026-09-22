import { Server } from 'lucide-react';
import { Badge } from '../../components/Badge';
import type { Tone } from '../../lib/status';
import { useUiStore } from '../../store/ui';
import type { ServerHint } from './deviceState';

/**
 * Em que MÁQUINA este aparelho roda. Nada no painel dizia isso: o cartão de um aparelho a seis metros daqui
 * tinha a mesma cara do emulador local, e "de uma tarefa descobrir onde ela roda" não tinha resposta (#61).
 *
 * Aparelho do central não ganha selo — ali o servidor é "aqui", e dez selos iguais na grade só fariam ruído.
 * O clique leva à Infraestrutura, que é onde o servidor tem estado, recursos e manutenção.
 */
export function ServerBadge({ server, size = 'sm', estatico }: {
  server: ServerHint | null;
  size?: 'sm' | 'md';
  /** Sem o atalho: usar quando o selo já está DENTRO de um botão (botão aninhado é HTML inválido). */
  estatico?: boolean;
}) {
  const setView = useUiStore((s) => s.setView);
  if (!server) return null;
  const tone: Tone = !server.enrolled ? 'danger' : !server.connected ? 'warning' : 'muted';
  const title = !server.enrolled ? `O servidor ${server.name} não está inscrito: este aparelho está sem ciclo de vida.`
    : !server.connected ? `O servidor ${server.name} não tem canal aberto agora — o que se vê deste aparelho pode estar velho.`
    : server.process ? `Hospedado em ${server.name} — processo lá: ${server.process}.`
    : `Hospedado em ${server.name}.`;
  const selo = (
    <Badge tone={tone} icon={Server} size={size} plain title={estatico ? title : undefined}>
      <span className="sr-only">Servidor: </span>{server.name}
    </Badge>
  );
  if (estatico) return selo;
  return (
    <button type="button" className="linkish" title={`${title} Abrir Infraestrutura.`}
            onClick={(e) => { e.stopPropagation(); setView('infraestrutura'); }}>
      {selo}
    </button>
  );
}
