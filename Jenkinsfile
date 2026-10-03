// Jenkins pipeline for Memory_System_Sim.
//
// Stages: clean old reports -> lint (ruff) -> tests (pytest, JUnit) with coverage (Cobertura) -> the
// performance and behaviour gate against ci/perf_baseline.json -> results.md ->
// an optional nightly load-latency sweep.
//
// Needs on the agent: Python 3.10+. Plugins: Pipeline, Git, JUnit, Coverage.

pipeline {
    agent any

    parameters {
        booleanParam(name: 'NIGHTLY', defaultValue: false, description: 'Also run the load-latency sweep')
        string(name: 'PERF_MARGIN', defaultValue: '0.25',
               description: 'Allowed slow-down in requests/s against ci/perf_baseline.json')
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '30'))
        timeout(time: 45, unit: 'MINUTES')
    }

    stages {
        // The workspace is reused between builds (it keeps the virtualenv and build caches), so
        // delete the previous build's reports first. Without this a build that fails before its
        // tests run publishes the last build's JUnit results as its own (Rust_DES_Kernel #4 did).
        stage('Clean reports') {
            steps {
                sh 'rm -f pytest-junit.xml coverage.xml perf_report.md sweep.csv'
            }
        }

        stage('Setup') {
            steps {
                sh '''
                    python3 -m venv .venv
                    .venv/bin/pip install -q -e ".[test]" ruff
                '''
            }
        }

        stage('Lint') {
            steps {
                sh '.venv/bin/ruff check src tests ci examples validation'
            }
        }

        stage('Tests') {
            steps {
                sh '.venv/bin/pytest --junitxml=pytest-junit.xml --cov=memsim --cov-report=xml:coverage.xml'
                recordCoverage(tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']], sourceCodeRetention: 'LAST_BUILD')
            }
        }

        stage('Performance and behaviour gate') {
            steps {
                sh ".venv/bin/python ci/perf_gate.py --margin ${params.PERF_MARGIN}"
            }
            post {
                always { archiveArtifacts artifacts: 'perf_report.md', allowEmptyArchive: true }
            }
        }

        stage('Results') {
            steps {
                sh '.venv/bin/python examples/results.py > /dev/null'
                archiveArtifacts artifacts: 'examples/results.md'
            }
        }

        stage('Nightly load sweep') {
            when { expression { params.NIGHTLY } }
            steps {
                sh '.venv/bin/python ci/load_sweep.py'
                archiveArtifacts artifacts: 'sweep.csv'
            }
        }
    }

    post {
        always {
            junit testResults: 'pytest-junit.xml', allowEmptyResults: true
        }
    }
}
