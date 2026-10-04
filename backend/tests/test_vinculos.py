"""A regra "conta vinculada é conta real" (ADR-055) num lugar só: `app/shared/vinculos.py`.

O agendador, a validação do aprendizado e o diagnóstico do 31.52 tinham cada um a sua cópia do SQL. Nível de prova:
`simulated` (harness).
"""
from __future__ import annotations

from app.shared.vinculos import aparelhos_com_vinculo_ativo, tem_vinculo_ativo

from .conftest import Harness


def test_o_vinculo_ativo_marca_o_aparelho_e_o_desfeito_nao(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    assert not tem_vinculo_ativo(st.db, "android-01") and aparelhos_com_vinculo_ativo(st.db) == set()
    perfil = st.social_repo.create_profile(username="conta_teste", first_name=None, last_name=None,
                                           display_name=None, birth_date=None, email=None, persona_id=None)
    st.social_repo.bind(perfil, "android-01", reason="teste")
    assert tem_vinculo_ativo(st.db, "android-01") and not tem_vinculo_ativo(st.db, "android-02")
    assert aparelhos_com_vinculo_ativo(st.db) == {"android-01"}
    # a mesma regra do repositório social (o `real_account` da rede lê por ele)
    assert [str(b["instance_id"]) for b in st.social_repo.profiles_of_instance("android-01")] == ["android-01"]
    st.db.execute("UPDATE device_profile_bindings SET active=0 WHERE instance_id=?", ("android-01",))
    assert not tem_vinculo_ativo(st.db, "android-01") and aparelhos_com_vinculo_ativo(st.db) == set()
