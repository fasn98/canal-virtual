version: "3.9"

services:

  redis:
    image: redis:7
    container_name: redis
    volumes:
      - ./volumes/redis:/data

  renderer:
    build: ./renderer
    container_name: renderer
    volumes:
      - ./renderer:/app
      - ./volumes/output:/opt/canal-virtual/volumes/output
      - ./volumes/assets:/opt/canal-virtual/volumes/assets
    depends_on:
      - redis
