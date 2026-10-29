# Elasticsearch z polskimi analizatorami: Morfologik (lematy, judgments_core/textindex.py)
# i Stempel (zapas). Wersja wtyczki Morfologik = wersja Elasticsearch.
FROM docker.elastic.co/elasticsearch/elasticsearch:9.5.3
RUN bin/elasticsearch-plugin install --batch \
        https://repo1.maven.org/maven2/pl/allegro/tech/elasticsearch/plugin/elasticsearch-analysis-morfologik/9.5.3/elasticsearch-analysis-morfologik-9.5.3.zip \
    && bin/elasticsearch-plugin install --batch analysis-stempel
