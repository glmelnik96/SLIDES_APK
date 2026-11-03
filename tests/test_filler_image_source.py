from htmlslides.library import TemplateLibrary
from htmlslides.models import SlidePlan
from htmlslides.pipeline.filler import fill_slide, SlideContent


class Client:
    def chat_json(self, *args, **kwargs):
        return SlideContent(content={
            'title': 'Проверка', 'image': 'https://images.example.test/photo.jpg'})


def test_generated_image_not_in_brief_uses_brand_default():
    slide = SlidePlan(index=1, type='title', template_id='cover-image',
                      content={'brief': 'Результаты квартала: 12 обновлений'})
    result = fill_slide(Client(), TemplateLibrary.load(), slide)
    assert not result.content.get('image')


def test_explicit_image_in_brief_is_preserved():
    slide = SlidePlan(index=1, type='title', template_id='cover-image',
                      content={'brief': 'Иллюстрация https://images.example.test/photo.jpg'})
    result = fill_slide(Client(), TemplateLibrary.load(), slide)
    assert result.content['image'] == 'https://images.example.test/photo.jpg'
