-- O rascunho nasce na porta de política e o efeito só é registrado no commit. Entre os dois pode haver horas de
-- espera por aprovação e até um reinício do backend, então o que o rascunho descobriu precisa de um lugar DURÁVEL.
--
-- `draft_meta` guarda o que não é o texto: os candidatos a memória (fatos que a contraparte afirmou) e a
-- justificativa da escolha. Sem isso, `memory.learn_from` nunca tem o que aprender — era por isso que
-- `memory_items` ficava vazio para sempre.
--
-- Fica FORA de `bindings` de propósito: `bindings` é o que o ator vê no prompt, e isto não é argumento de ação.
ALTER TABLE steps ADD COLUMN draft_meta TEXT;
