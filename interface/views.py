

from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .serializers import DeviceDataSerializer

@api_view(['POST'])
def create_device_data(request):
    try:
        serializer = DeviceDataSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({
                'success': True,
                'data': serializer.data,
                'message': 'Device data created successfully.'
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'success': False,
                'errors': serializer.errors,
                'message': 'Validation failed.'
            }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            'success': False,
            'errors': str(e),
            'message': 'An unexpected error occurred.'
        }, status=status.HTTP_200_OK)