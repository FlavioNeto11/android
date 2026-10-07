import { Eye } from 'lucide-react';
import { useState } from 'react';
import { Button } from '../../components/Button';
import { TextInput } from '../../components/Field';
import { estaMascarado, type CampoRef } from './exibicao';
import styles from './Training.module.css';

/**
 * 31.189: o campo de texto da proposta que pode ter dado da persona. Enquanto o texto é o que o central mandou e a cópia para exibir é
 * outra (dado da persona trocado por `{nome}`), o campo mostra a cópia, só para ler; para editar, a pessoa pede ("Mostrar para editar")
 * e aí vê e muda o texto de verdade. Nada é trocado sem ela pedir: o que vai na prévia e no salvar é sempre o texto da proposta.
 */
export function CampoMascarado({ rotulo, valor, onChange, referencia, placeholder, id, invalid, 'aria-describedby': describedBy }: {
  /** Nome acessível do campo (sem a parte do botão). */
  rotulo: string;
  valor: string;
  onChange: (novo: string) => void;
  referencia: CampoRef;
  placeholder?: string;
  id?: string;
  invalid?: boolean;
  'aria-describedby'?: string;
}) {
  const [revelado, setRevelado] = useState(false);
  if (!revelado && estaMascarado(valor, referencia)) {
    return (
      <span className={styles.campoMascarado} data-mascarado>
        <TextInput id={id} aria-label={rotulo} aria-describedby={describedBy} value={referencia.exibido} readOnly />
        <Button size="sm" variant="outline" icon={Eye} aria-label={`Mostrar para editar: ${rotulo}`} onClick={() => setRevelado(true)}>Mostrar para editar</Button>
      </span>
    );
  }
  return <TextInput id={id} aria-label={rotulo} aria-describedby={describedBy} invalid={invalid} value={valor} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />;
}
