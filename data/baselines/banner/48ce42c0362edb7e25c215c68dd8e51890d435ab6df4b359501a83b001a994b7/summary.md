# Baseline agregada auditada do banner

## Identidade e resultado

- Resultado: `passed`
- Fonte: `banner.csv`
- SHA-256 aprovado: `48ce42c0362edb7e25c215c68dd8e51890d435ab6df4b359501a83b001a994b7`
- Rodadas independentes: 2

## Expectativas

- Dimensão observada/esperada: 166796x26 / 166796x26
- Cardinalidade bruta anônima de `fault`: 151 / 151
- Contrato: passed

## Gates e classificações

| Indicador | Classificação | Passou | Achados |
| --- | --- | :---: | ---: |
| `integrity.source` | blocking | sim | 0 |
| `parsing.csv` | blocking | sim | 0 |
| `contract.banner` | blocking | sim | 0 |
| `expectation.dimensions` | blocking | sim | 0 |
| `expectation.fault_cardinality` | blocking | sim | 0 |
| `determinism.temporal_arithmetic` | blocking | sim | 0 |
| `sanitization.public_payload` | blocking | sim | 0 |
| `reconciliation.aggregate_counts` | blocking | sim | 0 |
| `classification.public_fields` | blocking | sim | 0 |
| `determinism.byte_equality` | blocking | sim | 0 |
| `quality.complete_duplicates` | alert | sim | 0 |
| `quality.repeated_ids` | alert | sim | 0 |
| `quality.conflicting_ids` | alert | sim | 0 |
| `quality.irregular_cadence` | alert | não | 165171 |
| `quality.temporal_gaps` | alert | não | 90868 |
| `quality.redundant_pairs` | alert | não | 808926 |
| `observation.temporal_period` | observation | sim | 1 |
| `observation.column_statistics` | observation | sim | 23 |
| `observation.iqr_outliers` | observation | sim | 190107 |
| `observation.anonymous_fault_distribution` | observation | sim | 151 |
| `observation.fault_imbalance` | observation | sim | 12998 |
| `observation.redundant_pair_consistency` | observation | sim | 25054 |

## Perfil agregado

- Período UTC agregado: 2026-04-30T17:17:41.549800Z a 2026-06-16T18:59:43.447238Z
- Cadência nominal (s): 2.000111
- Intervalos irregulares: 165171
- Lacunas: 90868
- Duplicatas completas excedentes: 0
- Grupos de IDs repetidos: 0
- Grupos de IDs conflitantes: 0

### Estatísticas numéricas e IQR

| Coluna | Finitos | Mínimo | Máximo | Média | IQR | Outliers IQR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `z_rms_velocity_in_s` | 166796 | 0.012300 | 1.460400 | 0.067988 | 0.015400 | 8912 |
| `z_rms_velocity_mm_s` | 166796 | 0.312000 | 37.095000 | 1.727654 | 0.391000 | 8947 |
| `temperature_f` | 166796 | 60.130000 | 92.870000 | 73.889391 | 5.380000 | 1646 |
| `temperature_c` | 166796 | 15.630000 | 33.820000 | 23.269597 | 2.990000 | 1646 |
| `x_rms_velocity_in_s` | 166796 | 0.017200 | 0.722300 | 0.098277 | 0.027200 | 3700 |
| `x_rms_velocity_mm_s` | 166796 | 0.438000 | 18.346000 | 2.497003 | 0.691000 | 3717 |
| `z_peak_acceleration_g` | 166796 | 0.021000 | 38.409000 | 0.583399 | 0.130000 | 12849 |
| `x_peak_acceleration_g` | 166796 | 0.022000 | 25.241000 | 0.721705 | 0.354000 | 3220 |
| `z_peak_vel_comp_freq_hz` | 166796 | 9.700000 | 632.300000 | 55.763103 | 2.500000 | 41747 |
| `x_peak_vel_comp_freq_hz` | 166796 | 9.700000 | 253.900000 | 57.789162 | 4.900000 | 27602 |
| `z_rms_acceleration_g` | 166796 | 0.015000 | 3.635000 | 0.120863 | 0.053000 | 7862 |
| `x_rms_acceleration_g` | 166796 | 0.020000 | 1.447000 | 0.134041 | 0.036000 | 7690 |
| `z_kurtosis` | 166796 | 2.031000 | 65.535000 | 2.651439 | 0.365000 | 7114 |
| `x_kurtosis` | 166796 | 2.063000 | 28.890000 | 2.778971 | 0.355000 | 3944 |
| `z_crest_factor` | 166796 | 2.378000 | 23.705000 | 3.719540 | 0.513000 | 5726 |
| `x_crest_factor` | 166796 | 2.499000 | 14.966000 | 3.847107 | 0.518000 | 5003 |
| `z_peak_velocity_in_s` | 166796 | 0.017300 | 2.065300 | 0.096170 | 0.021800 | 8946 |
| `z_peak_velocity_mm_s` | 166796 | 0.441000 | 52.460000 | 2.443478 | 0.553000 | 8945 |
| `x_peak_velocity_in_s` | 166796 | 0.024400 | 1.021400 | 0.139005 | 0.038400 | 3721 |
| `x_peak_velocity_mm_s` | 166796 | 0.619000 | 25.945000 | 3.531504 | 0.977000 | 3717 |
| `z_high_freq_rms_accel_g` | 166796 | 0.006000 | 4.162000 | 0.151378 | 0.031000 | 12152 |
| `x_high_freq_rms_accel_g` | 166796 | 0.006000 | 5.088000 | 0.185980 | 0.101000 | 1301 |
| `rpm` | 166796 | 0.000000 | 3000.000000 | 1179.785486 | 1500.000000 | 0 |

### Distribuição anônima de rótulos

| Categoria anônima | Contagem | Percentual |
| --- | ---: | ---: |
| categoria 1 | 13000 | 7.793952 |
| categoria 2 | 11808 | 7.079306 |
| categoria 3 | 10079 | 6.042711 |
| categoria 4 | 10000 | 5.995348 |
| categoria 5 | 10000 | 5.995348 |
| categoria 6 | 10000 | 5.995348 |
| categoria 7 | 9004 | 5.398211 |
| categoria 8 | 9000 | 5.395813 |
| categoria 9 | 9000 | 5.395813 |
| categoria 10 | 9000 | 5.395813 |
| categoria 11 | 6000 | 3.597209 |
| categoria 12 | 5738 | 3.440130 |
| categoria 13 | 4000 | 2.398139 |
| categoria 14 | 3998 | 2.396940 |
| categoria 15 | 3075 | 1.843569 |
| categoria 16 | 3012 | 1.805799 |
| categoria 17 | 3000 | 1.798604 |
| categoria 18 | 3000 | 1.798604 |
| categoria 19 | 3000 | 1.798604 |
| categoria 20 | 3000 | 1.798604 |
| categoria 21 | 3000 | 1.798604 |
| categoria 22 | 2999 | 1.798005 |
| categoria 23 | 2999 | 1.798005 |
| categoria 24 | 400 | 0.239814 |
| categoria 25 | 397 | 0.238015 |
| categoria 26 | 300 | 0.179860 |
| categoria 27 | 300 | 0.179860 |
| categoria 28 | 237 | 0.142090 |
| categoria 29 | 208 | 0.124703 |
| categoria 30 | 200 | 0.119907 |
| categoria 31 | 200 | 0.119907 |
| categoria 32 | 200 | 0.119907 |
| categoria 33 | 200 | 0.119907 |
| categoria 34 | 200 | 0.119907 |
| categoria 35 | 200 | 0.119907 |
| categoria 36 | 200 | 0.119907 |
| categoria 37 | 200 | 0.119907 |
| categoria 38 | 200 | 0.119907 |
| categoria 39 | 200 | 0.119907 |
| categoria 40 | 200 | 0.119907 |
| categoria 41 | 200 | 0.119907 |
| categoria 42 | 200 | 0.119907 |
| categoria 43 | 200 | 0.119907 |
| categoria 44 | 200 | 0.119907 |
| categoria 45 | 200 | 0.119907 |
| categoria 46 | 200 | 0.119907 |
| categoria 47 | 200 | 0.119907 |
| categoria 48 | 200 | 0.119907 |
| categoria 49 | 200 | 0.119907 |
| categoria 50 | 200 | 0.119907 |
| categoria 51 | 200 | 0.119907 |
| categoria 52 | 200 | 0.119907 |
| categoria 53 | 200 | 0.119907 |
| categoria 54 | 200 | 0.119907 |
| categoria 55 | 200 | 0.119907 |
| categoria 56 | 200 | 0.119907 |
| categoria 57 | 200 | 0.119907 |
| categoria 58 | 200 | 0.119907 |
| categoria 59 | 200 | 0.119907 |
| categoria 60 | 199 | 0.119307 |
| categoria 61 | 150 | 0.089930 |
| categoria 62 | 150 | 0.089930 |
| categoria 63 | 150 | 0.089930 |
| categoria 64 | 150 | 0.089930 |
| categoria 65 | 150 | 0.089930 |
| categoria 66 | 150 | 0.089930 |
| categoria 67 | 150 | 0.089930 |
| categoria 68 | 150 | 0.089930 |
| categoria 69 | 150 | 0.089930 |
| categoria 70 | 150 | 0.089930 |
| categoria 71 | 150 | 0.089930 |
| categoria 72 | 150 | 0.089930 |
| categoria 73 | 150 | 0.089930 |
| categoria 74 | 150 | 0.089930 |
| categoria 75 | 150 | 0.089930 |
| categoria 76 | 150 | 0.089930 |
| categoria 77 | 150 | 0.089930 |
| categoria 78 | 150 | 0.089930 |
| categoria 79 | 150 | 0.089930 |
| categoria 80 | 150 | 0.089930 |
| categoria 81 | 150 | 0.089930 |
| categoria 82 | 150 | 0.089930 |
| categoria 83 | 150 | 0.089930 |
| categoria 84 | 150 | 0.089930 |
| categoria 85 | 150 | 0.089930 |
| categoria 86 | 150 | 0.089930 |
| categoria 87 | 150 | 0.089930 |
| categoria 88 | 150 | 0.089930 |
| categoria 89 | 150 | 0.089930 |
| categoria 90 | 150 | 0.089930 |
| categoria 91 | 150 | 0.089930 |
| categoria 92 | 150 | 0.089930 |
| categoria 93 | 150 | 0.089930 |
| categoria 94 | 150 | 0.089930 |
| categoria 95 | 150 | 0.089930 |
| categoria 96 | 150 | 0.089930 |
| categoria 97 | 150 | 0.089930 |
| categoria 98 | 150 | 0.089930 |
| categoria 99 | 150 | 0.089930 |
| categoria 100 | 150 | 0.089930 |
| categoria 101 | 150 | 0.089930 |
| categoria 102 | 150 | 0.089930 |
| categoria 103 | 150 | 0.089930 |
| categoria 104 | 150 | 0.089930 |
| categoria 105 | 150 | 0.089930 |
| categoria 106 | 150 | 0.089930 |
| categoria 107 | 150 | 0.089930 |
| categoria 108 | 150 | 0.089930 |
| categoria 109 | 150 | 0.089930 |
| categoria 110 | 150 | 0.089930 |
| categoria 111 | 150 | 0.089930 |
| categoria 112 | 150 | 0.089930 |
| categoria 113 | 150 | 0.089930 |
| categoria 114 | 150 | 0.089930 |
| categoria 115 | 150 | 0.089930 |
| categoria 116 | 150 | 0.089930 |
| categoria 117 | 150 | 0.089930 |
| categoria 118 | 150 | 0.089930 |
| categoria 119 | 150 | 0.089930 |
| categoria 120 | 150 | 0.089930 |
| categoria 121 | 150 | 0.089930 |
| categoria 122 | 150 | 0.089930 |
| categoria 123 | 100 | 0.059953 |
| categoria 124 | 100 | 0.059953 |
| categoria 125 | 100 | 0.059953 |
| categoria 126 | 100 | 0.059953 |
| categoria 127 | 100 | 0.059953 |
| categoria 128 | 100 | 0.059953 |
| categoria 129 | 100 | 0.059953 |
| categoria 130 | 100 | 0.059953 |
| categoria 131 | 100 | 0.059953 |
| categoria 132 | 97 | 0.058155 |
| categoria 133 | 69 | 0.041368 |
| categoria 134 | 63 | 0.037771 |
| categoria 135 | 50 | 0.029977 |
| categoria 136 | 50 | 0.029977 |
| categoria 137 | 50 | 0.029977 |
| categoria 138 | 50 | 0.029977 |
| categoria 139 | 50 | 0.029977 |
| categoria 140 | 50 | 0.029977 |
| categoria 141 | 50 | 0.029977 |
| categoria 142 | 50 | 0.029977 |
| categoria 143 | 50 | 0.029977 |
| categoria 144 | 42 | 0.025180 |
| categoria 145 | 39 | 0.023382 |
| categoria 146 | 31 | 0.018586 |
| categoria 147 | 21 | 0.012590 |
| categoria 148 | 20 | 0.011991 |
| categoria 149 | 7 | 0.004197 |
| categoria 150 | 2 | 0.001199 |
| categoria 151 | 2 | 0.001199 |

### Pares redundantes

| Par | Comparáveis | Consistentes | Inconsistentes |
| --- | ---: | ---: | ---: |
| `z_rms_velocity_in_s` → `z_rms_velocity_mm_s` | 166796 | 1373 | 165423 |
| `temperature_c` → `temperature_f` | 166796 | 19723 | 147073 |
| `x_rms_velocity_in_s` → `x_rms_velocity_mm_s` | 166796 | 1293 | 165503 |
| `z_peak_velocity_in_s` → `z_peak_velocity_mm_s` | 166796 | 1347 | 165449 |
| `x_peak_velocity_in_s` → `x_peak_velocity_mm_s` | 166796 | 1318 | 165478 |

## Reconciliações

| Código | Escopo | Esperado | Observado | Passou |
| --- | --- | ---: | ---: | :---: |
| `columns.expected_partition` | — | 26 | 26 | sim |
| `columns.observed_partition` | — | 26 | 26 | sim |
| `columns.observed_count` | id | 166796 | 166796 | sim |
| `columns.missing_count` | id | 0 | 0 | sim |
| `columns.observed_count` | created_at | 166796 | 166796 | sim |
| `columns.missing_count` | created_at | 0 | 0 | sim |
| `columns.observed_count` | z_rms_velocity_in_s | 166796 | 166796 | sim |
| `columns.missing_count` | z_rms_velocity_in_s | 0 | 0 | sim |
| `numeric.finite_partition` | z_rms_velocity_in_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_rms_velocity_in_s | 166796 | 8912 | sim |
| `columns.observed_count` | z_rms_velocity_mm_s | 166796 | 166796 | sim |
| `columns.missing_count` | z_rms_velocity_mm_s | 0 | 0 | sim |
| `numeric.finite_partition` | z_rms_velocity_mm_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_rms_velocity_mm_s | 166796 | 8947 | sim |
| `columns.observed_count` | temperature_f | 166796 | 166796 | sim |
| `columns.missing_count` | temperature_f | 0 | 0 | sim |
| `numeric.finite_partition` | temperature_f | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | temperature_f | 166796 | 1646 | sim |
| `columns.observed_count` | temperature_c | 166796 | 166796 | sim |
| `columns.missing_count` | temperature_c | 0 | 0 | sim |
| `numeric.finite_partition` | temperature_c | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | temperature_c | 166796 | 1646 | sim |
| `columns.observed_count` | x_rms_velocity_in_s | 166796 | 166796 | sim |
| `columns.missing_count` | x_rms_velocity_in_s | 0 | 0 | sim |
| `numeric.finite_partition` | x_rms_velocity_in_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_rms_velocity_in_s | 166796 | 3700 | sim |
| `columns.observed_count` | x_rms_velocity_mm_s | 166796 | 166796 | sim |
| `columns.missing_count` | x_rms_velocity_mm_s | 0 | 0 | sim |
| `numeric.finite_partition` | x_rms_velocity_mm_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_rms_velocity_mm_s | 166796 | 3717 | sim |
| `columns.observed_count` | z_peak_acceleration_g | 166796 | 166796 | sim |
| `columns.missing_count` | z_peak_acceleration_g | 0 | 0 | sim |
| `numeric.finite_partition` | z_peak_acceleration_g | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_peak_acceleration_g | 166796 | 12849 | sim |
| `columns.observed_count` | x_peak_acceleration_g | 166796 | 166796 | sim |
| `columns.missing_count` | x_peak_acceleration_g | 0 | 0 | sim |
| `numeric.finite_partition` | x_peak_acceleration_g | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_peak_acceleration_g | 166796 | 3220 | sim |
| `columns.observed_count` | z_peak_vel_comp_freq_hz | 166796 | 166796 | sim |
| `columns.missing_count` | z_peak_vel_comp_freq_hz | 0 | 0 | sim |
| `numeric.finite_partition` | z_peak_vel_comp_freq_hz | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_peak_vel_comp_freq_hz | 166796 | 41747 | sim |
| `columns.observed_count` | x_peak_vel_comp_freq_hz | 166796 | 166796 | sim |
| `columns.missing_count` | x_peak_vel_comp_freq_hz | 0 | 0 | sim |
| `numeric.finite_partition` | x_peak_vel_comp_freq_hz | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_peak_vel_comp_freq_hz | 166796 | 27602 | sim |
| `columns.observed_count` | z_rms_acceleration_g | 166796 | 166796 | sim |
| `columns.missing_count` | z_rms_acceleration_g | 0 | 0 | sim |
| `numeric.finite_partition` | z_rms_acceleration_g | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_rms_acceleration_g | 166796 | 7862 | sim |
| `columns.observed_count` | x_rms_acceleration_g | 166796 | 166796 | sim |
| `columns.missing_count` | x_rms_acceleration_g | 0 | 0 | sim |
| `numeric.finite_partition` | x_rms_acceleration_g | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_rms_acceleration_g | 166796 | 7690 | sim |
| `columns.observed_count` | z_kurtosis | 166796 | 166796 | sim |
| `columns.missing_count` | z_kurtosis | 0 | 0 | sim |
| `numeric.finite_partition` | z_kurtosis | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_kurtosis | 166796 | 7114 | sim |
| `columns.observed_count` | x_kurtosis | 166796 | 166796 | sim |
| `columns.missing_count` | x_kurtosis | 0 | 0 | sim |
| `numeric.finite_partition` | x_kurtosis | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_kurtosis | 166796 | 3944 | sim |
| `columns.observed_count` | z_crest_factor | 166796 | 166796 | sim |
| `columns.missing_count` | z_crest_factor | 0 | 0 | sim |
| `numeric.finite_partition` | z_crest_factor | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_crest_factor | 166796 | 5726 | sim |
| `columns.observed_count` | x_crest_factor | 166796 | 166796 | sim |
| `columns.missing_count` | x_crest_factor | 0 | 0 | sim |
| `numeric.finite_partition` | x_crest_factor | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_crest_factor | 166796 | 5003 | sim |
| `columns.observed_count` | z_peak_velocity_in_s | 166796 | 166796 | sim |
| `columns.missing_count` | z_peak_velocity_in_s | 0 | 0 | sim |
| `numeric.finite_partition` | z_peak_velocity_in_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_peak_velocity_in_s | 166796 | 8946 | sim |
| `columns.observed_count` | z_peak_velocity_mm_s | 166796 | 166796 | sim |
| `columns.missing_count` | z_peak_velocity_mm_s | 0 | 0 | sim |
| `numeric.finite_partition` | z_peak_velocity_mm_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_peak_velocity_mm_s | 166796 | 8945 | sim |
| `columns.observed_count` | x_peak_velocity_in_s | 166796 | 166796 | sim |
| `columns.missing_count` | x_peak_velocity_in_s | 0 | 0 | sim |
| `numeric.finite_partition` | x_peak_velocity_in_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_peak_velocity_in_s | 166796 | 3721 | sim |
| `columns.observed_count` | x_peak_velocity_mm_s | 166796 | 166796 | sim |
| `columns.missing_count` | x_peak_velocity_mm_s | 0 | 0 | sim |
| `numeric.finite_partition` | x_peak_velocity_mm_s | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_peak_velocity_mm_s | 166796 | 3717 | sim |
| `columns.observed_count` | z_high_freq_rms_accel_g | 166796 | 166796 | sim |
| `columns.missing_count` | z_high_freq_rms_accel_g | 0 | 0 | sim |
| `numeric.finite_partition` | z_high_freq_rms_accel_g | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | z_high_freq_rms_accel_g | 166796 | 12152 | sim |
| `columns.observed_count` | x_high_freq_rms_accel_g | 166796 | 166796 | sim |
| `columns.missing_count` | x_high_freq_rms_accel_g | 0 | 0 | sim |
| `numeric.finite_partition` | x_high_freq_rms_accel_g | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | x_high_freq_rms_accel_g | 166796 | 1301 | sim |
| `columns.observed_count` | fault | 166796 | 166796 | sim |
| `columns.missing_count` | fault | 0 | 0 | sim |
| `columns.observed_count` | rpm | 166796 | 166796 | sim |
| `columns.missing_count` | rpm | 0 | 0 | sim |
| `numeric.finite_partition` | rpm | 166796 | 166796 | sim |
| `numeric.iqr_outliers_within_finite` | rpm | 166796 | 0 | sim |
| `timestamps.value_partition` | created_at | 166796 | 166796 | sim |
| `timestamps.interval_count` | created_at | 165796 | 165796 | sim |
| `labels.value_partition` | fault | 166796 | 166796 | sim |
| `labels.histogram_total` | fault | 166796 | 166796 | sim |
| `labels.positive_category_count` | fault | 151 | 151 | sim |
| `unit_pairs.availability_partition` | z_rms_velocity_in_s-&gt;z_rms_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.consistency_partition` | z_rms_velocity_in_s-&gt;z_rms_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.availability_partition` | temperature_c-&gt;temperature_f | 166796 | 166796 | sim |
| `unit_pairs.consistency_partition` | temperature_c-&gt;temperature_f | 166796 | 166796 | sim |
| `unit_pairs.availability_partition` | x_rms_velocity_in_s-&gt;x_rms_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.consistency_partition` | x_rms_velocity_in_s-&gt;x_rms_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.availability_partition` | z_peak_velocity_in_s-&gt;z_peak_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.consistency_partition` | z_peak_velocity_in_s-&gt;z_peak_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.availability_partition` | x_peak_velocity_in_s-&gt;x_peak_velocity_mm_s | 166796 | 166796 | sim |
| `unit_pairs.consistency_partition` | x_peak_velocity_in_s-&gt;x_peak_velocity_mm_s | 166796 | 166796 | sim |
| `duplicates.key_available` | id | 1 | 1 | sim |
| `duplicates.complete_excess_denominator` | — | 166796 | 0 | sim |
| `duplicates.complete_group_denominator` | — | 166796 | 0 | sim |
| `duplicates.key_excess_denominator` | id | 166796 | 0 | sim |
| `duplicates.key_group_denominator` | id | 166796 | 0 | sim |
| `duplicates.conflicting_group_denominator` | id | 0 | 0 | sim |
| `duplicates.conflicting_row_denominator` | id | 166796 | 0 | sim |
| `versions.contract_profile` | — | 2 | 2 | sim |
| `classifications.indicator_registry` | — | 22 | 22 | sim |
| `markdown.regeneration` | — | 1 | 1 | sim |
