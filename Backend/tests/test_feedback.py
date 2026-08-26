"""Pruebas del feedback del chat. No tocan Postgres: la sesión se sustituye."""

import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.api.deps import get_db, get_feedback_service
from app.main import app
from app.schemas.chat import ChatRequest
from app.models.chat_interaccion import ChatInteraccion
from app.schemas.feedback import VotoRequest
from app.services.feedback_service import FeedbackService

FIN = {"respuesta": "la efectividad es 72%", "traza": []}


class SesionFalsa:
    """Recoge la sentencia en vez de ejecutarla. Con `falla` simula la base caída."""

    def __init__(self, sentencias: list, falla: bool = False):
        self.sentencias = sentencias
        self.falla = falla

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, sentencia):
        if self.falla:
            raise ConnectionError("la base no responde")
        self.sentencias.append(sentencia)
        return self

    def scalar_one(self):
        return uuid.UUID("11111111-1111-1111-1111-111111111111")

    async def commit(self):
        pass


def servicio(falla: bool = False) -> tuple[FeedbackService, list]:
    sentencias: list = []
    return FeedbackService(lambda: SesionFalsa(sentencias, falla)), sentencias


def parametros(sentencia) -> dict:
    return sentencia.compile(dialect=postgresql.dialect()).params


# --- Qué se guarda -------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_posicion_sale_de_contar_preguntas():
    """Tercera pregunta del hilo: n_interaccion 3, aunque el saludo vaya delante."""
    feedback, sentencias = servicio()
    request = ChatRequest(
        messages=[
            {"role": "assistant", "content": "Hola"},
            {"role": "user", "content": "una"},
            {"role": "assistant", "content": "ahí va"},
            {"role": "user", "content": "dos"},
            {"role": "assistant", "content": "ahí va"},
            {"role": "user", "content": "tres"},
        ]
    )

    await feedback.registrar(request, FIN)

    valores = parametros(sentencias[0])
    assert valores["n_interaccion"] == 3
    assert valores["pregunta"] == "tres"
    assert valores["respuesta"] == FIN["respuesta"]


@pytest.mark.asyncio
async def test_reintentar_la_misma_pregunta_reemplaza_y_borra_el_voto():
    """El voto anterior opinaba sobre una respuesta que ya no es esta."""
    feedback, sentencias = servicio()

    await feedback.registrar(ChatRequest(**{"messages": [{"role": "user", "content": "x"}]}), FIN)

    sql = str(sentencias[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql
    assert "chat_interaccion_posicion_unica" in sql


@pytest.mark.asyncio
async def test_si_la_base_falla_no_lanza():
    """La regla dura de toda la feature: el chat responde igual."""
    feedback, _ = servicio(falla=True)

    guardado = await feedback.registrar(
        ChatRequest(**{"messages": [{"role": "user", "content": "x"}]}), FIN
    )

    assert guardado is None


# --- El contrato del voto ------------------------------------------------------


def test_el_motivo_solo_acompana_al_pulgar_abajo():
    with pytest.raises(ValidationError):
        VotoRequest(voto="util", motivo="dato_incorrecto")


def test_retirar_el_voto_no_admite_motivo():
    with pytest.raises(ValidationError):
        VotoRequest(voto=None, motivo="no_entendio")


def test_retirar_el_voto_a_secas_es_valido():
    assert VotoRequest(voto=None).voto is None


# --- El endpoint ---------------------------------------------------------------


class FeedbackFalso:
    def __init__(self, existe: bool = True):
        self.existe = existe
        self.votos: list[VotoRequest] = []

    async def aplicar_voto(self, sesion, interaccion_id, voto) -> bool:
        self.votos.append(voto)
        return self.existe

    async def detalle(self, sesion, interaccion_id):
        return None


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def override(doble: FeedbackFalso) -> None:
    app.dependency_overrides[get_feedback_service] = lambda: doble
    # El doble no usa la sesión, pero la dependencia se resuelve igual: sin esto
    # la prueba abriría una conexión contra la base de producción.
    app.dependency_overrides[get_db] = lambda: None


def test_votar_devuelve_204(client):
    doble = FeedbackFalso()
    override(doble)

    respuesta = client.patch(
        f"/api/v1/feedback/{uuid.uuid4()}",
        json={"voto": "inutil", "motivo": "dato_incorrecto", "comentario": "la cifra no cuadra"},
    )

    assert respuesta.status_code == 204
    assert doble.votos[0].motivo == "dato_incorrecto"


def test_votar_sobre_un_id_que_no_existe_da_404(client):
    override(FeedbackFalso(existe=False))

    respuesta = client.patch(f"/api/v1/feedback/{uuid.uuid4()}", json={"voto": "util"})

    assert respuesta.status_code == 404


def test_un_voto_incoherente_da_422(client):
    override(FeedbackFalso())

    respuesta = client.patch(
        f"/api/v1/feedback/{uuid.uuid4()}", json={"voto": "util", "motivo": "no_entendio"}
    )

    assert respuesta.status_code == 422


# --- La cola de revisión -------------------------------------------------------


def interaccion(**campos) -> ChatInteraccion:
    """Una fila en memoria, sin pasar por la base."""
    base = {
        "id": uuid.uuid4(),
        "conversacion_id": uuid.uuid4(),
        "n_interaccion": 1,
        "pregunta": "¿efectividad de Villa Sabita?",
        "respuesta": "72%",
        "vista": {"municipio": "GALAPA"},
        "traza": [
            {"herramienta": "efectividad", "argumentos": {}, "resultado": {}},
            {"herramienta": "ranking", "argumentos": {}, "resultado": {}},
        ],
        "voto": None,
        "motivo": None,
        "comentario": None,
        "creado_en": datetime(2026, 8, 25, 10, 15, tzinfo=timezone.utc),
        "calificado_en": None,
    }
    return ChatInteraccion(**{**base, **campos})


class SesionDeLectura:
    """Devuelve las filas que se le den, sin tocar Postgres."""

    def __init__(self, filas: list[ChatInteraccion]):
        self.filas = filas
        self.consultas: list = []

    async def execute(self, consulta):
        self.consultas.append(consulta)
        return self

    def scalars(self):
        return self

    def all(self):
        return self.filas

    async def get(self, _modelo, _id):
        return self.filas[0] if self.filas else None


@pytest.mark.asyncio
async def test_la_lista_resume_las_herramientas_y_no_manda_la_traza():
    """En 50 filas la traza pesaría megabytes; para elegir cuál abrir basta el nombre."""
    feedback, _ = servicio()
    sesion = SesionDeLectura([interaccion()])

    filas = await feedback.listar(sesion)

    assert filas[0].herramientas == ["efectividad", "ranking"]
    assert not hasattr(filas[0], "traza")


@pytest.mark.asyncio
async def test_el_detalle_si_trae_la_traza_completa():
    """Es lo que separa «la herramienta calculó mal» de «el modelo redactó mal»."""
    feedback, _ = servicio()
    fila = interaccion()

    detalle = await feedback.detalle(SesionDeLectura([fila]), fila.id)

    assert len(detalle.traza) == 2
    assert detalle.traza[0]["herramienta"] == "efectividad"
    assert detalle.vista == {"municipio": "GALAPA"}


@pytest.mark.asyncio
async def test_la_cola_se_ordena_por_lo_calificado_no_por_lo_respondido():
    """Un voto de hoy sobre una respuesta vieja tiene que salir arriba."""
    feedback, _ = servicio()
    sesion = SesionDeLectura([])

    await feedback.listar(sesion)

    sql = str(sesion.consultas[0])
    assert "calificado_en DESC" in sql
    assert sql.index("calificado_en DESC") < sql.index("creado_en DESC")


@pytest.mark.asyncio
async def test_el_filtro_de_hasta_incluye_el_dia_entero():
    """«Del 1 al 15» tiene que traer lo del 15; con el corte a medianoche no lo haría."""
    feedback, _ = servicio()
    sesion = SesionDeLectura([])

    await feedback.listar(sesion, hasta=date(2026, 8, 15))

    parametros_ = sesion.consultas[0].compile(dialect=postgresql.dialect()).params
    assert any(str(v).startswith("2026-08-15 23:59") for v in parametros_.values())


def test_el_detalle_de_un_id_que_no_existe_da_404(client):
    override(FeedbackFalso())

    assert client.get(f"/api/v1/feedback/{uuid.uuid4()}").status_code == 404


# --- Los ejemplos que se le pasan al modelo ------------------------------------


class SesionDeEjemplos:
    """Cuenta cuántas veces se consultó, para ver si el caché sirve de algo."""

    def __init__(self, filas, falla: bool = False):
        self.filas = filas
        self.falla = falla
        self.consultas = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, _consulta):
        self.consultas += 1
        if self.falla:
            raise ConnectionError("la base no responde")
        return self

    def all(self):
        return self.filas


@pytest.mark.asyncio
async def test_los_ejemplos_se_cachean_entre_turnos():
    """Se leen en cada pregunta de cada usuario: sin caché sería una consulta por turno."""
    sesion = SesionDeEjemplos([("¿y X?", "72%")])
    feedback = FeedbackService(lambda: sesion)

    primera = await feedback.ejemplos()
    segunda = await feedback.ejemplos()

    assert primera == segunda == [{"pregunta": "¿y X?", "respuesta": "72%"}]
    assert sesion.consultas == 1


@pytest.mark.asyncio
async def test_la_respuesta_del_ejemplo_se_recorta():
    """Cada ejemplo se paga en tokens en todas las preguntas, no solo en las parecidas."""
    from app.services.feedback_service import LARGO_EJEMPLO

    sesion = SesionDeEjemplos([("¿y X?", "x" * (LARGO_EJEMPLO + 500))])
    feedback = FeedbackService(lambda: sesion)

    assert len((await feedback.ejemplos())[0]["respuesta"]) == LARGO_EJEMPLO


@pytest.mark.asyncio
async def test_si_la_base_falla_se_responde_sin_ejemplos():
    """Peor respuesta, pero respuesta: quedarse mudo sería peor."""
    feedback = FeedbackService(lambda: SesionDeEjemplos([], falla=True))

    assert await feedback.ejemplos() == []
