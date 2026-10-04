# GoalEarn Project 01 Dataset — Intent Data Foundation

## Purpose
This dataset is designed for the GoalEarn Analytics Engineer — Revenue & Data Products
Project 01: Intent Data Foundation.

The learner's job is NOT to clean everything perfectly by hand. The goal is to:
Raw Data → Staging → Standardized Events → User/Organization Behavior → Intent-ready Data.

## Data provenance
- `raw_circleci/`: CircleCI-shaped synthetic data modeled after public CircleCI API concepts.
  It is NOT an export of CircleCI's private customer data and should not be represented as such.
- `source/`: synthetic business/user context created for the simulation.
- The public CircleCI API documentation is the reference for the API concepts and fields:
  https://circleci.com/docs/api/v2/
  https://circleci.com/docs/guides/toolkit/api-developers-guide/

## Intentional data quality issues
The dataset intentionally includes duplicates, missing foreign keys, orphan references,
unknown event names, future timestamps, invalid statuses, and negative job durations.
These issues are part of the exercise.

## Recommended workflow
1. Profile every source table.
2. Document grain.
3. Identify PK/FK candidates.
4. Build staging models.
5. Define accepted event taxonomy.
6. Create data quality tests.
7. Build an intent-ready event model.
8. Aggregate behavior to user and organization levels.
9. Document assumptions and unresolved issues.

## Core business question
"What product behaviors can be reliably modeled as inputs for future user-intent analysis?"

## Do not build yet
Do not build the final PQL score or Salesforce activation model in Project 01.
Those belong to Projects 02 and 03.

## Suggested tools
SQL, dbt, PostgreSQL or DuckDB, Git, optional Snowflake.

## Dataset scale
Approximately:
- 500 users
- 100 organizations
- 200 projects
- 3,000+ pipelines
- 7,000 workflows
- 20,000 jobs
- 12,000+ product events
