import unittest

from app.ai.text_inference import infer_service, infer_vehicle


class VehicleInferenceTests(unittest.TestCase):
    def test_uses_accented_dedicated_vehicle_section(self):
        self.assertEqual(
            infer_vehicle("VEÍCULO IDENTIFICADO: Hyundai HB20\nCONFIANÇA: alta"),
            "Hyundai HB20",
        )

    def test_accepts_unaccented_dedicated_vehicle_section(self):
        self.assertEqual(
            infer_vehicle("VEICULO IDENTIFICADO: Fiat Uno"),
            "Fiat Uno",
        )

    def test_known_model_is_normalized(self):
        self.assertEqual(infer_vehicle("A picape S-10 aparece nas imagens."), "S-10")
        self.assertEqual(infer_vehicle("O T-Cross foi identificado."), "T-Cross")

    def test_uncertain_vehicle_is_not_inferred_from_placeholder(self):
        self.assertEqual(infer_vehicle("VEÍCULO IDENTIFICADO: não identificado"), "")
        self.assertEqual(infer_vehicle(""), "")


class ServiceInferenceTests(unittest.TestCase):
    def test_infers_lavagem_e_higienizacao_with_correct_accents(self):
        self.assertEqual(
            infer_service("O serviço realizado foi lavagem e higienização interna."),
            "Lavagem e Higienização",
        )

    def test_infers_higienizacao_interna(self):
        self.assertEqual(
            infer_service("Foi feita higienização interna."),
            "Higienização Interna",
        )

    def test_infers_lavagem_externa(self):
        self.assertEqual(
            infer_service("Executamos lavagem externa detalhada."),
            "Lavagem Externa",
        )

    def test_unknown_service_remains_empty(self):
        self.assertEqual(infer_service("Não foi possível determinar o serviço."), "")


if __name__ == "__main__":
    unittest.main()
