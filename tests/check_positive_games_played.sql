SELECT bus_date,
       venue_code,
       egm_description,
       games_played_sum
FROM {{ ref('stg_game_performance') }}
WHERE games_played_sum <= 0