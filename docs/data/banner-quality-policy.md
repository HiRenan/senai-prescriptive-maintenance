# Política de qualidade e outliers do banner

## Identidade e alcance

- `policy_id`: `51190f2c6662c2ffb236d887f4bb43f1f7cb03f98dfc387441cc5410dcd3838b`
- Versão da política: 1
- Contrato de colunas: v2
- Esquema do profiler: v1
- Alcance: declaração e consulta; nenhuma linha é alterada ou removida.

## Precedências imutáveis

- Motivos: `required_value_missing` > `non_finite` > `invalid_physical_domain` > `conflicting_duplicate` > `inconsistent_redundant_unit` > `identical_duplicate` > `iqr_outlier`
- Ação efetiva: `reject` > `correct_deterministically` > `map` > `flag` > `keep`

A precedência escolhe somente a ação principal e a ordem de serialização. Todos os matches e motivos concorrentes permanecem na decisão auditável.

## Matriz pública de regras

| Regra | Papel e coluna(s) | Unidade(s) | Motivo | Ação | Severidade | Precedência | Operador, limiar e inclusividade | Justificativa | Origem |
| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| `required.identifier.missing` | identifier: id | 1 | `required_value_missing` | `reject` | `error` | 10 | is_missing; não aplicável; inclusivo: não aplicável | O identificador é obrigatório no contrato; sua ausência impede rastrear a ocorrência. | banner_contract.v2.nullable |
| `required.event_timestamp.missing` | event_timestamp: created_at | UTC | `required_value_missing` | `reject` | `error` | 20 | is_missing; não aplicável; inclusivo: não aplicável | O instante é obrigatório no contrato; sem ele não há contexto temporal verificável. | banner_contract.v2.nullable |
| `required.measurement.missing` | measurement: z_rms_velocity_in_s, z_rms_velocity_mm_s, temperature_f, temperature_c, x_rms_velocity_in_s, x_rms_velocity_mm_s, z_peak_acceleration_g, x_peak_acceleration_g, z_peak_vel_comp_freq_hz, x_peak_vel_comp_freq_hz, z_rms_acceleration_g, x_rms_acceleration_g, z_kurtosis, x_kurtosis, z_crest_factor, x_crest_factor, z_peak_velocity_in_s, z_peak_velocity_mm_s, x_peak_velocity_in_s, x_peak_velocity_mm_s, z_high_freq_rms_accel_g, x_high_freq_rms_accel_g, rpm | in/s, mm/s, °F, °C, g, Hz, 1, rpm | `required_value_missing` | `reject` | `error` | 30 | is_missing; não aplicável; inclusivo: não aplicável | As medições declaradas são obrigatórias e não recebem imputação nesta etapa. | SEN-27.out_of_scope.statistical_imputation, banner_contract.v2.nullable |
| `required.raw_label.missing` | raw_label: fault | 1 | `required_value_missing` | `reject` | `error` | 40 | is_missing; não aplicável; inclusivo: não aplicável | O rótulo bruto não vazio é obrigatório; a taxonomia e a imputação permanecem fora do escopo. | banner_contract.v2.fault_domain, banner_contract.v2.nullable |
| `measurement.non_finite` | measurement: z_rms_velocity_in_s, z_rms_velocity_mm_s, temperature_f, temperature_c, x_rms_velocity_in_s, x_rms_velocity_mm_s, z_peak_acceleration_g, x_peak_acceleration_g, z_peak_vel_comp_freq_hz, x_peak_vel_comp_freq_hz, z_rms_acceleration_g, x_rms_acceleration_g, z_kurtosis, x_kurtosis, z_crest_factor, x_crest_factor, z_peak_velocity_in_s, z_peak_velocity_mm_s, x_peak_velocity_in_s, x_peak_velocity_mm_s, z_high_freq_rms_accel_g, x_high_freq_rms_accel_g, rpm | in/s, mm/s, °F, °C, g, Hz, 1, rpm | `non_finite` | `reject` | `error` | 50 | not_finite; não aplicável; inclusivo: não aplicável | NaN e infinito não representam uma medição finita e são excluídos da população estatística. | banner_contract.v2.float_domain, banner_profile.v1.finite_population |
| `physical.nonnegative.lower_bound` | measurement: z_rms_velocity_in_s, z_rms_velocity_mm_s, x_rms_velocity_in_s, x_rms_velocity_mm_s, z_peak_vel_comp_freq_hz, x_peak_vel_comp_freq_hz, z_rms_acceleration_g, x_rms_acceleration_g, z_high_freq_rms_accel_g, x_high_freq_rms_accel_g | in/s, mm/s, Hz, g | `invalid_physical_domain` | `reject` | `error` | 60 | &lt;; 0; inclusivo: não | O contrato declara zero como limite físico inferior válido para essas grandezas. | banner_contract.v2.physical_lower_bound |
| `physical.temperature_f.absolute_zero` | measurement: temperature_f | °F | `invalid_physical_domain` | `reject` | `error` | 70 | &lt;; -459.67; inclusivo: não | O contrato adota o zero absoluto em Fahrenheit como limite inferior inclusivo do domínio válido. | banner_contract.v2.physical_lower_bound |
| `physical.temperature_c.absolute_zero` | measurement: temperature_c | °C | `invalid_physical_domain` | `reject` | `error` | 80 | &lt;; -273.15; inclusivo: não | O contrato adota o zero absoluto em Celsius como limite inferior inclusivo do domínio válido. | banner_contract.v2.physical_lower_bound |
| `duplicate.conflicting.reject` | record: id | 1 | `conflicting_duplicate` | `reject` | `error` | 90 | repeated_key_with_different_record; all_non_key_columns; inclusivo: não aplicável | A mesma chave com conteúdos diferentes é ambígua e bloqueia uma disposição automática. | banner_baseline.v1.profile.duplicates.key_columns, banner_profile.v1.conflicting_key_groups |
| `unit.inconsistent.ambiguous` | measurement: z_rms_velocity_in_s, z_rms_velocity_mm_s, temperature_f, temperature_c, x_rms_velocity_in_s, x_rms_velocity_mm_s, z_peak_velocity_in_s, z_peak_velocity_mm_s, x_peak_velocity_in_s, x_peak_velocity_mm_s | in/s, mm/s, °F, °C | `inconsistent_redundant_unit` | `reject` | `error` | 100 | outside_tolerance_without_unique_trusted_counterpart; declared_relation; inclusivo: não | O contrato preserva as duas colunas como independentes; sem uma única contraparte comprovada, escolher qual valor alterar seria arbitrário. | banner_contract.v2.source_units, banner_profile.v1.redundant_unit_pairs |
| `unit.inconsistent.deterministic` | measurement: z_rms_velocity_in_s, z_rms_velocity_mm_s, temperature_f, temperature_c, x_rms_velocity_in_s, x_rms_velocity_mm_s, z_peak_velocity_in_s, z_peak_velocity_mm_s, x_peak_velocity_in_s, x_peak_velocity_mm_s | in/s, mm/s, °F, °C | `inconsistent_redundant_unit` | `correct_deterministically` | `warning` | 110 | outside_tolerance_with_unique_trusted_counterpart; declared_relation; inclusivo: não | A correção é permitida somente quando uma contraparte independente e única prova o valor de origem e a fórmula declarada determina o resultado sem escolha. | SEN-27.deterministic_correction_guard, banner_contract.v2.source_units, banner_profile.v1.redundant_unit_pairs |
| `duplicate.identical.map_identity` | record: id | 1 | `identical_duplicate` | `map` | `warning` | 120 | repeated_key_with_identical_record; all_contract_columns; inclusivo: não aplicável | A ocorrência é associada à mesma identidade de conteúdo para auditoria; cada duplicata permanece como registro distinto, sem deduplicação nem remoção. | SEN-27.identical_duplicate_auditability, SEN-27.identical_duplicate_preservation, banner_profile.v1.complete_duplicates |
| `outlier.iqr.flag` | measurement: z_rms_velocity_in_s, z_rms_velocity_mm_s, temperature_f, temperature_c, x_rms_velocity_in_s, x_rms_velocity_mm_s, z_peak_acceleration_g, x_peak_acceleration_g, z_peak_vel_comp_freq_hz, x_peak_vel_comp_freq_hz, z_rms_acceleration_g, x_rms_acceleration_g, z_kurtosis, x_kurtosis, z_crest_factor, x_crest_factor, z_peak_velocity_in_s, z_peak_velocity_mm_s, x_peak_velocity_in_s, x_peak_velocity_mm_s, z_high_freq_rms_accel_g, x_high_freq_rms_accel_g, rpm | in/s, mm/s, °F, °C, g, Hz, 1, rpm | `iqr_outlier` | `flag` | `warning` | 130 | outside_iqr_fences; Q1 - 1.5 * IQR; Q3 + 1.5 * IQR; inclusivo: não | Raridade estatística pode carregar sinal industrial; a regra apenas sinaliza e preserva o registro. | SEN-27.outlier_preservation, banner_profile.v1.iqr |

## IQR congelado

- Q1: 0.25
- Q3: 0.75
- Método: `linear_type_7`
- Multiplicador: 1.5
- População: `finite_values_only`
- Fronteiras: valor `<` Q1 - k * IQR ou `>` Q3 + k * IQR; igualdade não é outlier.
- Ação: `flag`; o registro é preservado.

## Relações redundantes

| Relação | Fórmula | Tolerância absoluta | Tolerância relativa | Inclusiva |
| --- | --- | ---: | ---: | :---: |
| `temperature_c_to_temperature_f` | `right = left * 1.8 + 32` | 1e-06 | 1e-06 | sim |
| `x_peak_velocity_in_s_to_mm_s` | `right = left * 25.4` | 1e-06 | 1e-06 | sim |
| `x_rms_velocity_in_s_to_mm_s` | `right = left * 25.4` | 1e-06 | 1e-06 | sim |
| `z_peak_velocity_in_s_to_mm_s` | `right = left * 25.4` | 1e-06 | 1e-06 | sim |
| `z_rms_velocity_in_s_to_mm_s` | `right = left * 25.4` | 1e-06 | 1e-06 | sim |

## Comparação agregada com a baseline rastreada

A baseline aprovada contém 166796 registros e 26 colunas. A comparação abaixo usa somente contagens agregadas já publicadas.

| Motivo | Evidência agregada | Leitura da política |
| --- | ---: | --- |
| `required_value_missing` | 0 células | Rejeitar por papel da coluna. |
| `non_finite` | 0 células | Rejeitar; não entram no IQR. |
| `invalid_physical_domain` | 0 células | Rejeitar pelos limites do contrato. |
| `identical_duplicate` | 0 grupos / 0 excedentes | Mapear a identidade de auditoria e manter cada registro. |
| `conflicting_duplicate` | 0 grupos / 0 registros | Rejeitar. |
| `inconsistent_redundant_unit` | 808926 comparações em 5 pares | Bloquear sem prova única; corrigir somente com contraparte independentemente comprovada. |
| `iqr_outlier` | 190107 ocorrências célula-coluna em 23 colunas | Sinalizar e preservar. |

## Limitações

- A comparação com a baseline usa somente agregados rastreados e não acessa a fonte original.
- A política não avalia, altera, remove, imputa ou publica registros individuais.
- O IQR sinaliza raridade estatística e não prova erro físico.
- O contrato não define uma contraparte autoritativa nos pares redundantes; sem prova independente única, a inconsistência bloqueia.
- As ocorrências IQR são contagens por célula-coluna e podem se sobrepor no mesmo registro; não equivalem a uma contagem de linhas.
- A baseline agregada não prova qual contraparte redundante está correta e não autoriza correção automática.
