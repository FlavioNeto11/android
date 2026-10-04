import { useState } from 'react';
import { api, toApiError } from '../../api/client';
import { Button } from '../../components/Button';
import { Field, TextInput } from '../../components/Field';
import { validarCartao } from './anexos';
import styles from './Canais.module.css';

/**
 * "Anexar ao cartão" (28.24 F4), no padrão do `DecisaoInline` do Aprendizado: o campo e o botão de confirmar na mesma linha,
 * dentro do próprio anexo, nunca num modal. O botão diz o efeito (é um arquivo que sai para o Trello, um sistema externo)
 * e só ele manda `confirmar: true` à rota. Quem usa esta tela é o dono; a rota recusa o que não for dele.
 */
export function AnexarAoCartao({ anexoId, onFeito, onCancelar }: {
  anexoId: number;
  /** `jaEstava`: a rota achou o mesmo arquivo no cartão e não anexou de novo (F5). */
  onFeito: (jaEstava: boolean) => void;
  onCancelar: () => void;
}) {
  const [texto, setTexto] = useState('');
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const conferido = validarCartao(texto);
  const invalido = conferido.ok ? null : conferido.erro;

  const enviar = async () => {
    if (!conferido.ok || enviando) return;
    setEnviando(true);
    setErro(null);
    try {
      const r = await api.canaisAnexarAoCartao(anexoId, conferido.card);
      onFeito(r.ja_estava === true);
    } catch (e) {
      // A mensagem da rota já vem em português e sem chave nem token (ex.: "Esse cartão não é de um quadro que a Central espelha.").
      setErro(toApiError(e).message);
      setEnviando(false);
    }
  };

  return (
    <form className={styles.anexar} onSubmit={(e) => { e.preventDefault(); void enviar(); }}>
      <p className={styles.anexarResumo}>
        O arquivo vai para o cartão no Trello, que é um sistema fora da Central. Só cartões dos quadros que a Central espelha.
      </p>
      <Field label="Cartão do Trello" hint="O link do cartão (Compartilhar › Copiar link), o código de 8 caracteres dele ou o id." error={erro}>
        {({ id, describedBy, invalid }) => (
          <div className={styles.anexarLinha}>
            <TextInput id={id} aria-describedby={describedBy} invalid={invalid} value={texto} maxLength={300} autoFocus mono
                       placeholder="Ex.: https://trello.com/c/AbCdEf12" className={styles.anexarTexto}
                       onChange={(e) => setTexto(e.target.value)} />
            <div className={styles.anexarAcoes}>
              <Button type="submit" size="sm" variant="primary" loading={enviando} disabledReason={invalido}>
                Confirmar e anexar ao cartão
              </Button>
              <Button size="sm" variant="ghost" onClick={onCancelar} disabled={enviando}>Cancelar</Button>
            </div>
          </div>
        )}
      </Field>
      {invalido && !enviando && texto.trim() ? <p className={styles.anexarPendente}>{invalido}</p> : null}
    </form>
  );
}
