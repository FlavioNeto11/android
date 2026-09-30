import { MessageSquare } from 'lucide-react';
import { useState } from 'react';
import { api } from '../../api/client';
import type { SocialInteraction } from '../../api/types';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { EmptyState } from '../../components/EmptyState';
import { Carregando, useLista, useVersaoAoVivo } from './detalheComum';
import type { Pessoa } from './pessoa';
import styles from './Profiles.module.css';
import { InteractionTimeline, TimelineFilter } from './Timeline';

// ---------------------------------------------------------------- interações
export function AbaInteracoes({ profile, appId = null }: { profile: Pessoa; appId?: string | null }) {
  const versao = useVersaoAoVivo(profile);
  const [itens] = useLista<SocialInteraction>(() => api.listInteractions(profile.id, 30, appId), [profile.id, versao, appId]);
  const [filtro, setFiltro] = useState<string | null>(null);
  if (itens === null) return <Carregando />;
  if (itens.length === 0) {
    return (
      <EmptyState icon={MessageSquare} title="Sem interações" hint="Aqui fica o que esta persona fez e recebeu.">
        Nada registrado ainda.
      </EmptyState>
    );
  }
  const tipos = [...new Set(itens.map((i) => i.type))];
  const filtrados = filtro ? itens.filter((i) => i.type === filtro) : itens;
  return (
    <Card>
      <CardHeader title="Histórico social" subtitle="O que esta persona fez e recebeu, do mais recente ao mais antigo." />
      <CardBody>
        <TimelineFilter tipos={tipos} ativo={filtro} onChange={setFiltro} />
        {filtrados.length === 0 ? (
          <p className={styles.detail}>Nada deste tipo ainda.</p>
        ) : (
          <InteractionTimeline itens={filtrados} />
        )}
      </CardBody>
    </Card>
  );
}
