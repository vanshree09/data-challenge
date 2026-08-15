{{
    config(
        materialized='table',
        alias='stg_game_performance'
    )
}}

SELECT
    bus_date, 
    venue_code, 
    egm_description, 
    manufacturer, 
    fp, 
    turnover_sum, 
    gmp_sum, 
    games_played_sum
FROM {{ source('raw', 'game_performance') }}