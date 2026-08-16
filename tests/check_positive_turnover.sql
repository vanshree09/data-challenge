SELECT bus_date,
       venue_code,
       egm_description,
       turnover_sum
FROM {{ ref('stg_game_performance') }}
WHERE turnover_sum <= 0