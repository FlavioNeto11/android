import { useState } from 'react';
import { api } from '../../api/client';
import { Button } from '../../components/Button';
import { Card, CardBody, CardHeader } from '../../components/Card';
import { toastError } from '../../store/toasts';
import { ehTeste } from './filtroPersonas';
import { nomeDe, type Pessoa } from './pessoa';
import styles from './Profiles.module.css';

/**
 * 31.315 (adendo v1.139): marcar ou desmarcar a persona como de TESTE (`PATCH /api/personas/{id}` com `teste`). A marca só
 * muda o que a lista mostra e o que o automático e o em massa escolhem; citada por nome ou id num comando, a persona de teste
 * serve como qualquer outra. Por isso desmarcar não pede confirmação: nada externo muda.
 */
export function MarcaDeTeste({ profile, onChanged }: { profile: Pessoa; onChanged: () => Promise<void> }) {
  const [salvando, setSalvando] = useState(false);
  const teste = ehTeste(profile);

  async function trocar() {
    setSalvando(true);
    try {
      await api.updatePersona(profile.id, { teste: !teste });
      await onChanged();
    } catch (e) {
      toastError(teste ? 'Não foi possível desmarcar a persona de teste' : 'Não foi possível marcar a persona como de teste', e);
    } finally {
      setSalvando(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Persona de teste"
                  subtitle="Fica fora da lista de personas até você pedir e não entra nas escolhas automáticas nem nas ações em massa." />
      <CardBody>
        <div className={styles.groupPicker}>
          <p className={styles.detail}>
            {teste ? `${nomeDe(profile)} está marcada como de teste.` : `${nomeDe(profile)} é uma persona comum.`}
          </p>
          <Button size="sm" variant="outline" disabled={salvando} onClick={() => void trocar()}>
            {teste ? 'Desmarcar como de teste' : 'Marcar como de teste'}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
