"""Generación de imágenes con fal.ai.

fal_client lee la clave de la env var FAL_KEY automáticamente.
Ejecuta `python media.py` para una prueba de humo (genera una imagen y muestra la URL).
"""
import urllib.request

import fal_client

import config


def generate_image(prompt, model=None, image_size="landscape_16_9", **extra):
    """Texto -> imagen. Devuelve la URL de la imagen generada.

    `image_size`: "square", "portrait_4_3", "landscape_16_9", etc.
    `extra`: cualquier argumento adicional del modelo (num_images, seed, ...).
    """
    model = model or config.FAL_IMAGE_MODEL
    arguments = {"prompt": prompt, "image_size": image_size}
    arguments.update(extra)
    result = fal_client.subscribe(model, arguments=arguments)
    return result["images"][0]["url"]


def download(url, path):
    """Descarga una URL a disco. Devuelve la ruta."""
    urllib.request.urlretrieve(url, path)
    return path


if __name__ == "__main__":
    url = generate_image("a cute robot mascot for a Spanish hackathon, flat vector, vibrant")
    print("Imagen generada:", url)
