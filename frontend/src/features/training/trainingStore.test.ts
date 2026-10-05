import { beforeEach, expect, it } from 'vitest';
import { useTrainingStore } from './trainingStore';

beforeEach(() => useTrainingStore.setState({ gravando: {}, recusadas: {} }));

it('recusa fora da gravação não conta; durante conta; ao terminar a gravação o contador zera', () => {
  const s = () => useTrainingStore.getState();
  s().registrarRecusa('android-01');
  expect(s().recusadas['android-01']).toBeUndefined();

  s().definirGravando('android-01', true);
  s().registrarRecusa('android-01');
  s().registrarRecusa('android-01');
  expect(s().recusadas['android-01']).toBe(2);
  s().registrarRecusa('android-02');
  expect(s().recusadas['android-02']).toBeUndefined();

  s().definirGravando('android-01', false);
  expect(s().recusadas['android-01']).toBeUndefined();
});
